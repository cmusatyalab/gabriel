package edu.cmu.cs.gabriel.client

import java.io.File
import java.io.IOException
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.time.Duration
import java.util.concurrent.TimeUnit

/**
 * Launches the real Python Gabriel server (`server/main.py`) as a
 * subprocess, listening for gRPC client connections on [port]. Used by
 * [RealServerCategory] tests that exercise [GabrielClient] against an actual
 * server rather than a fake.
 */
class RealGabrielServer private constructor(
    val port: Int,
    private val process: Process,
    private val logFile: File,
) : AutoCloseable {

    override fun close() {
        process.destroy()
        if (!process.waitFor(5, TimeUnit.SECONDS)) {
            process.destroyForcibly()
        }
        logFile.delete()
    }

    companion object {
        private val pythonExecutable = System.getProperty("gabriel.server.python", "python3")
        private val mainPyPath = System.getProperty("gabriel.server.mainPy")
            ?: error(
                "system property gabriel.server.mainPy not set " +
                    "(see gabriel-client/build.gradle.kts)"
            )

        /**
         * Starts a server, waiting until it's accepting client connections.
         * Reuses [port] if given (e.g. to restart a server that a test
         * killed), otherwise picks a free one.
         */
        fun start(numTokens: Int = 2, port: Int? = null): RealGabrielServer {
            val clientPort = port ?: findFreePort()
            val enginePort = findFreePort()
            val prometheusPort = findFreePort()
            val logFile = File.createTempFile("gabriel-server", ".log").apply { deleteOnExit() }

            val process = ProcessBuilder(
                pythonExecutable, mainPyPath,
                "--transport", "grpc",
                "--client_port", clientPort.toString(),
                "--engine_port", enginePort.toString(),
                "--prometheus_port", prometheusPort.toString(),
                "--tokens", numTokens.toString(),
            )
                .redirectErrorStream(true)
                .redirectOutput(logFile)
                .start()

            try {
                waitUntilListening(clientPort, Duration.ofSeconds(15))
            } catch (e: IllegalStateException) {
                process.destroyForcibly()
                throw IllegalStateException(
                    "${e.message}\n--- server log (${logFile.absolutePath}) ---\n" +
                        logFile.readText(),
                    e,
                )
            }
            return RealGabrielServer(clientPort, process, logFile)
        }

        private fun findFreePort(): Int = ServerSocket(0).use { it.localPort }

        private fun waitUntilListening(port: Int, timeout: Duration) {
            val deadline = System.nanoTime() + timeout.toNanos()
            while (System.nanoTime() < deadline) {
                try {
                    Socket().use { it.connect(InetSocketAddress("localhost", port), 200) }
                    return
                } catch (e: IOException) {
                    Thread.sleep(100)
                }
            }
            error("server did not start listening on port $port within $timeout")
        }
    }
}
