package edu.cmu.cs.gabriel.client

import io.grpc.ManagedChannel
import io.grpc.okhttp.OkHttpChannelBuilder
import java.util.concurrent.TimeUnit
import kotlin.time.Duration.Companion.seconds
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.experimental.categories.Category

/**
 * Exercises [GabrielClient] against a real [RealGabrielServer] subprocess,
 * over a real socket, rather than a fake/in-process server. See
 * [RealServerCategory] for how to run just these tests.
 */
@Category(RealServerCategory::class)
class GabrielClientRealServerTest {

    private val scope = CoroutineScope(Dispatchers.Default)
    private var server: RealGabrielServer? = null
    private var channel: ManagedChannel? = null

    @After
    fun tearDown() {
        scope.cancel()
        channel?.shutdownNow()?.awaitTermination(5, TimeUnit.SECONDS)
        server?.close()
    }

    private fun channelTo(port: Int): ManagedChannel =
        OkHttpChannelBuilder.forAddress("localhost", port)
            .usePlaintext()
            .build()
            .also { channel = it }

    // Block bodies (not `= runBlocking { ... }`) are deliberate: JUnit4
    // requires @Test methods to return void, but an expression-bodied
    // function infers its return type from runBlocking's last statement
    // (here, a non-Unit ConnectionState from `first { }`), which JUnit
    // rejects at class-validation time with "should be void".
    @Test
    fun `connects and registers with a real server`() {
        runBlocking {
            val realServer = RealGabrielServer.start().also { server = it }
            val client = GabrielClient(
                channel = channelTo(realServer.port),
                inputProducers = emptyList(),
                consumer = {},
            )
            scope.launch { client.run() }

            withTimeout(10.seconds) {
                client.connectionState.first { it == GabrielClient.ConnectionState.CONNECTED }
            }
            // No cognitive engine connected, so the server should report none.
            assertTrue(client.engineIds.value.isEmpty())
        }
    }

    @Test
    fun `reconnects after the server process restarts`() {
        runBlocking {
            var realServer = RealGabrielServer.start().also { server = it }
            val client = GabrielClient(
                channel = channelTo(realServer.port),
                inputProducers = emptyList(),
                consumer = {},
                reconnectInterval = 0.5.seconds,
            )
            scope.launch { client.run() }

            withTimeout(10.seconds) {
                client.connectionState.first { it == GabrielClient.ConnectionState.CONNECTED }
            }

            realServer.close()
            withTimeout(10.seconds) {
                client.connectionState.first { it == GabrielClient.ConnectionState.DISCONNECTED }
            }

            // Restart a fresh server process on the same port; the client's
            // automatic reconnect loop (and the underlying gRPC channel's own
            // transport-level reconnect) should pick it back up on its own,
            // with no action from the test beyond waiting.
            realServer = RealGabrielServer.start(port = realServer.port).also { server = it }
            withTimeout(15.seconds) {
                client.connectionState.first { it == GabrielClient.ConnectionState.CONNECTED }
            }
        }
    }
}
