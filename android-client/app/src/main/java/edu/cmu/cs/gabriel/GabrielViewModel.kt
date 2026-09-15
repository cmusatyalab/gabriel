package edu.cmu.cs.gabriel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import edu.cmu.cs.gabriel.client.GabrielClient
import edu.cmu.cs.gabriel.client.InputProducer
import gabriel_protocol.v1.Gabriel
import io.grpc.ManagedChannel
import io.grpc.okhttp.OkHttpChannelBuilder
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Owns the lifecycle of a [GabrielClient] and its underlying gRPC channel,
 * so the connection survives configuration changes (e.g. rotation) and is
 * torn down exactly once, in [onCleared].
 */
class GabrielViewModel : ViewModel() {

    private var channel: ManagedChannel? = null
    private var clientJob: Job? = null

    private val _connectionState =
        MutableStateFlow(GabrielClient.ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<GabrielClient.ConnectionState> =
        _connectionState.asStateFlow()

    /**
     * Connects to [endpoint] (a plaintext "host:port" gRPC target) using a
     * single placeholder [InputProducer] that sends a text frame once a
     * token is available. Replace this producer with one that captures real
     * sensor data (camera, microphone, etc.) for an actual application, and
     * use TLS for anything beyond local testing.
     */
    fun connect(endpoint: String) {
        if (channel != null) return

        val channel = OkHttpChannelBuilder.forTarget(endpoint)
            .usePlaintext()
            .build()
        this.channel = channel

        val demoProducer = InputProducer(
            name = "demo",
            targetEngineIds = listOf("demo"),
        ) {
            Gabriel.InputFrame.newBuilder()
                .setPayloadType(Gabriel.PayloadType.TEXT)
                .setStringPayload("hello from Android")
                .build()
        }

        val client = GabrielClient(
            channel = channel,
            inputProducers = listOf(demoProducer),
            consumer = { result -> onResult(result) },
        )

        clientJob = viewModelScope.launch {
            launch { client.connectionState.collect { state -> _connectionState.value = state } }
            client.run()
        }
    }

    fun disconnect() {
        clientJob?.cancel()
        clientJob = null
        channel?.shutdownNow()
        channel = null
        _connectionState.value = GabrielClient.ConnectionState.DISCONNECTED
    }

    private fun onResult(result: Gabriel.Result) {
        // Placeholder: real applications should dispatch this to whatever
        // renders results.
    }

    override fun onCleared() {
        disconnect()
    }
}
