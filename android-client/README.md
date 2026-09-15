# Gabriel Android Client

Two modules:

- **`gabriel-client`** — an Android library with no UI dependencies. This is
  the reusable client, mirroring the [Go](../go-client) and
  [Python](../python-client) client libraries: it talks to the server over
  gRPC using the protobuf messages defined in [protocol](../protocol).
- **`app`** — a minimal Jetpack Compose demo app that depends on
  `gabriel-client`.

## Requirements

- Android Studio (or the command-line tools) with SDK Platform 37.2 and
  Build-Tools 37.0.0 installed.
- JDK 17+ for the Gradle daemon (AGP 9 requires it).
- A local `local.properties` file pointing `sdk.dir` at your Android SDK
  (Android Studio creates this automatically on first sync).

## Building

```
./gradlew :app:assembleDebug
```

The Gabriel protocol buffers are consumed directly from
[`protocol/proto`](../protocol/proto) by the `gabriel-client` module, so no
protobuf/gRPC code needs to be checked in here; it's generated at build time
by the `protobuf-gradle-plugin` into `gabriel-client/build/generated/source/proto`.

## Versions and a note on AGP 9

This targets current-as-of-September-2026 tooling: AGP 9.4.0, Kotlin 2.4.20,
Gradle 9.7.1, compileSdk 37 (targetSdk stays at 36, the level Google Play
currently mandates — compileSdk and targetSdk are allowed to diverge).

AGP 9 made Kotlin support built-in and introduced a new DSL, deprecating the
standalone `org.jetbrains.kotlin.android` plugin and the classic
`kotlinOptions`/`BaseExtension` APIs. Both are new this AGP generation, and
compatibility with the third-party codegen plugins this project depends on
(`protobuf-gradle-plugin`, `grpc-kotlin`) isn't yet established. `gradle.properties`
therefore sets `android.newDsl=false` and `android.builtInKotlin=false` — the
officially documented opt-outs, supported through AGP 10 — to keep the
classic, well-trodden plugin wiring. Revisit this once those plugins confirm
support for the new DSL.

## Testing

`./gradlew :gabriel-client:testDebugUnitTest` runs `gabriel-client`'s
ordinary local unit tests (plain JVM, no emulator needed).

A separate `./gradlew :gabriel-client:integrationTest` runs tests tagged
`RealServerCategory` (excluded from the task above), which launch the real
Python server ([`server/main.py`](../server/main.py)) as a subprocess and
drive `GabrielClient` against it over a real socket with a real
`OkHttpChannelBuilder` channel — see `GabrielClientRealServerTest` and the
`RealGabrielServer` helper that starts/stops it. These need a Python 3
interpreter with the server's dependencies installed (see
[`server/pyproject.toml`](../server/pyproject.toml)); point at a specific
interpreter with `GABRIEL_TEST_PYTHON=/path/to/python ./gradlew :gabriel-client:integrationTest`
if `python3` on your `PATH` isn't the right one.

## Usage

`edu.cmu.cs.gabriel.client.GabrielClient` is the core client. Construct it
with:

- a `ManagedChannel` you've configured (target, TLS, keepalive, etc. — see
  `io.grpc.okhttp.OkHttpChannelBuilder` or `io.grpc.android.AndroidChannelBuilder`,
  both available to consumers of this library),
- a list of `InputProducer`s, each a named source of frames targeting one or
  more cognitive engines,
- a `consumer` callback invoked with every `Result` returned by the server.

Call `GabrielClient.run()` from a coroutine; it suspends for the client's
lifetime, registering with the server and exchanging frames, and
automatically reconnects if the stream is lost. `connectionState` and
`engineIds` are exposed as `StateFlow`s for observing connection status and
the set of cognitive engines currently connected to the server.

`app`'s `GabrielViewModel` shows the intended pattern: it owns the
`ManagedChannel` and `GabrielClient` for the lifetime of the `ViewModel`
(surviving configuration changes), and tears both down exactly once in
`onCleared()`. `MainScreen` is a small Compose screen that opens a plaintext
channel to a user-entered `host:port` endpoint using a placeholder text-frame
producer. Replace that producer with one that captures real sensor data
(camera, microphone, etc.) for an actual application, and use TLS
credentials for anything beyond local testing.
