package edu.cmu.cs.gabriel.client

import android.util.Log
import gabriel_protocol.v1.Gabriel
import gabriel_protocol.v1.GabrielClientServiceGrpcKt
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.StatusException
import java.util.UUID
import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.SendChannel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore

private const val TAG = "GabrielClient"

/** Metadata keys the server uses to correlate a client's stream to a session. */
private const val SESSION_ID_METADATA_KEY = "session-id"
private const val STREAM_ROLE_METADATA_KEY = "stream-role"

/**
 * The server requires every stream to declare a role: "control" (exactly
 * one per session, carrying Registration and, in this single-stream client,
 * Input too) or "producer" (an optional, additional upload-only stream per
 * input producer, purely so gRPC's HTTP/2 transport can interleave that
 * producer's frames separately -- not used here, since this client sends
 * every producer's input over its one control stream).
 */
private const val STREAM_ROLE_CONTROL = "control"

/** How long [GabrielClient.run] waits between attempts to reestablish a lost stream. */
val DEFAULT_RECONNECT_INTERVAL: Duration = 2.seconds

/** How long [GabrielClient.run] waits between retries of an unacknowledged Registration. */
val DEFAULT_REGISTRATION_RETRY_INTERVAL: Duration = 2.seconds

/**
 * A Gabriel client that communicates with the server over gRPC, using the
 * `GabrielClientService.ClientSession` bidirectional stream defined in the
 * Gabriel protocol.
 *
 * Call [run] from a coroutine (typically tied to a `ViewModel`'s
 * `viewModelScope` or another lifecycle-aware scope) to connect and start
 * exchanging frames. [run] suspends until its coroutine is cancelled,
 * automatically reconnecting with [reconnectInterval] between attempts if the
 * stream to the server is lost.
 *
 * @param channel the gRPC channel to use, already configured with the
 *   desired target, TLS/keepalive settings, etc. The caller owns its
 *   lifecycle and is responsible for shutting it down.
 * @param inputProducers the frame sources for this client. Names must be
 *   unique.
 * @param consumer called on every [Gabriel.Result] returned by the server.
 *   Must not block; dispatch elsewhere if handling a result is expensive.
 * @param clientInfo optional client-specific information sent to the server
 *   once per session as part of the client's Registration message, made
 *   available to engines alongside any input this client subsequently sends.
 */
class GabrielClient(
    private val channel: ManagedChannel,
    private val inputProducers: List<InputProducer>,
    private val consumer: (Gabriel.Result) -> Unit,
    private val clientInfo: com.google.protobuf.Any? = null,
    private val reconnectInterval: Duration = DEFAULT_RECONNECT_INTERVAL,
    private val registrationRetryInterval: Duration = DEFAULT_REGISTRATION_RETRY_INTERVAL,
) {
    init {
        val duplicateName = inputProducers
            .groupingBy { it.name }
            .eachCount()
            .entries
            .firstOrNull { it.value > 1 }
        require(duplicateName == null) {
            "duplicate InputProducer name \"${duplicateName?.key}\""
        }
    }

    enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED }

    private val stub = GabrielClientServiceGrpcKt.GabrielClientServiceCoroutineStub(channel)

    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private val _engineIds = MutableStateFlow<Set<String>>(emptySet())
    val engineIds: StateFlow<Set<String>> = _engineIds.asStateFlow()

    /**
     * Connects to the server and runs this client until the calling
     * coroutine is cancelled. Suspends for the duration of the client's
     * lifetime; run it in its own coroutine (e.g. `launch { client.run() }`).
     */
    suspend fun run() {
        while (true) {
            _connectionState.value = ConnectionState.CONNECTING
            try {
                runSession()
            } catch (e: CancellationException) {
                throw e
            } catch (e: StatusException) {
                Log.w(TAG, "disconnected from server; will reconnect", e)
            } catch (e: io.grpc.StatusRuntimeException) {
                Log.w(TAG, "disconnected from server; will reconnect", e)
            } finally {
                _connectionState.value = ConnectionState.DISCONNECTED
                _engineIds.value = emptySet()
            }
            delay(reconnectInterval)
        }
    }

    /**
     * Runs a single session: opens the stream, registers, and exchanges
     * frames/results until the stream ends or an error occurs.
     */
    private suspend fun runSession(): Unit = coroutineScope {
        val outgoing = Channel<Gabriel.FromClient>(Channel.UNLIMITED)
        val tokenPools = mutableMapOf<String, Semaphore>()

        val headers = Metadata().apply {
            put(
                Metadata.Key.of(SESSION_ID_METADATA_KEY, Metadata.ASCII_STRING_MARSHALLER),
                UUID.randomUUID().toString(),
            )
            put(
                Metadata.Key.of(STREAM_ROLE_METADATA_KEY, Metadata.ASCII_STRING_MARSHALLER),
                STREAM_ROLE_CONTROL,
            )
        }
        val incoming = stub.clientSession(outgoing.receiveAsFlow(), headers)

        val registrationJob = launch { registrationLoop(outgoing) }

        // Producer coroutines are launched lazily, only once REGISTERED
        // arrives, since a valid token pool per producer requires knowing
        // num_tokens_per_producer from the server first.
        var producersStarted = false

        incoming.collect { toClient ->
            when (toClient.messageTypeCase) {
                Gabriel.ToClient.MessageTypeCase.REGISTERED -> {
                    _connectionState.value = ConnectionState.CONNECTED
                    _engineIds.value = toClient.registered.engineIdsList.toSet()
                    if (!producersStarted) {
                        producersStarted = true
                        registrationJob.cancel()
                        for (producer in inputProducers) {
                            val tokenPool = Semaphore(toClient.registered.numTokensPerProducer)
                            tokenPools[producer.name] = tokenPool
                            launch { producerLoop(producer, tokenPool, outgoing) }
                        }
                    }
                }

                Gabriel.ToClient.MessageTypeCase.ENGINE_IDS_UPDATE -> {
                    _engineIds.value = toClient.engineIdsUpdate.engineIdsList.toSet()
                }

                Gabriel.ToClient.MessageTypeCase.RESULT_WRAPPER -> {
                    val wrapper = toClient.resultWrapper
                    if (wrapper.returnToken) {
                        tokenPools[wrapper.producerId]?.release()
                    }
                    consumer(wrapper.result)
                }

                else -> Unit
            }
        }
    }

    /**
     * Sends a Registration message and retries every
     * [registrationRetryInterval] until this coroutine is cancelled (which
     * happens once REGISTERED is received and the session's other coroutines
     * take over the outgoing channel).
     */
    private suspend fun registrationLoop(outgoing: SendChannel<Gabriel.FromClient>) {
        val registrationBuilder = Gabriel.FromClient.Registration.newBuilder()
        if (clientInfo != null) {
            registrationBuilder.setClientInfo(clientInfo)
        }
        val message = Gabriel.FromClient.newBuilder()
            .setRegistration(registrationBuilder.build())
            .build()

        while (true) {
            outgoing.send(message)
            delay(registrationRetryInterval)
        }
    }

    /**
     * Sends frames from [producer] as tokens become available, until this
     * coroutine is cancelled.
     */
    private suspend fun producerLoop(
        producer: InputProducer,
        tokenPool: Semaphore,
        outgoing: SendChannel<Gabriel.FromClient>,
    ) {
        var frameId = 0L
        while (true) {
            tokenPool.acquire()
            val frame = producer.nextFrame()
            val input = Gabriel.FromClient.Input.newBuilder()
                .setFrameId(frameId++)
                .setProducerId(producer.name)
                .addAllTargetEngineIds(producer.targetEngineIds)
                .setInputFrame(frame)
                .build()
            outgoing.send(Gabriel.FromClient.newBuilder().setInput(input).build())
        }
    }
}
