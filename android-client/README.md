# Gabriel Android Library

Android library for communicating with a
[Gabriel Server](https://github.com/cmusatyalab/gabriel-server-common).

## Usage

Add the lines `implementation 'edu.cmu.cs.gabriel:client:0.2.0'` and
`implementation 'edu.cmu.cs.gabriel:protocol:0.1.15'` to your app's build.gradle
file.
Your project must include either the `jcenter()` repository or the
`mavenCentral()` repository.

Your app must allow cleartext traffic. If your app does not have an Android
Network Security Config, you must sepcify `android:usesCleartextTraffic="true"`
in the
[application element](https://developer.android.com/guide/topics/manifest/application-element)
of your Manifest file.
If your app has an Android Network Security Config, you must allow cleartext
traffic using this
config. See
[here](https://developer.android.com/guide/topics/manifest/application-element#usesCleartextTraffic)
for more details.

Create an instance of `edu.cmu.cs.gabriel.client.comm.ServerComm`. The
constructor takes an instance of `edu.cmu.cs.gabriel.client.function.Consumer`
that gets run whenever a new result is available from the server.
[Example](https://github.com/cmusatyalab/openrtist/blob/dfc3e246031a3006bdf0f5fcaa192ed0a5237ab8/android-client/app/src/main/java/edu/cmu/cs/gabriel/network/OpenrtistComm.java#L18).
The `ServerComm` constructor also takes an `onDisconnect` `Consuer` that gets
called when there is a connection problem. This `onDisconnect` consumer should
start the process of presenting an error message to the user, cleaning up the
app state, and then bringing the user back to a screen to modify connection
settings.

Send messages to the server using `ServerComm`'s `sendNoWait`, `sendBlocking`,
or `sendSupplier` methods.
[Example](https://github.com/cmusatyalab/openrtist/blob/dfc3e246031a3006bdf0f5fcaa192ed0a5237ab8/android-client/app/src/main/java/edu/cmu/cs/gabriel/network/BaseComm.java#L152).
You should check the result from all three of these functions.
The most common reason that `sendBlocking` and `sendSupplier` will fail is that
the server does not have any engines that consume the specified filter. You can
explicitly check this using `ServerComm#acceptsInputForFilter`.

### Measurement

This library includes instrumentation to measure the average RTT and FPS. These
results can be sent using
[Log](https://developer.android.com/reference/android/util/Log) or written to a
file. You can also implement your own `Consumer<RttFps>` that writes results to
a database and/or logs additional information.

I recommend creating a separate
[product flavor](https://developer.android.com/studio/build/build-variants#product-flavors)
for measuerments. Create an instance of `MeasurementServerComm` instead of
`ServerComm`. Pass an instance of `LogRttFpsConsumer` to the constructor of
`MeasurementServerComm` to send measurements to log output. Use
`CsvLogRttFpsConsumer` to write measurements to a file.
[Example](https://github.com/cmusatyalab/openrtist/blob/dfc3e246031a3006bdf0f5fcaa192ed0a5237ab8/android-client/app/src/measurement/java/edu/cmu/cs/gabriel/network/MeasurementComm.java#L24)

## Publishing Changes to Maven Central

1. Update the VERSION_NAME parameter in `gradle.properties`. You can add
   `-SNAPSHOT` to the end of the version number to get a snapshot published
   instead.
2. Open the `Gradle` tab in the top right of Android studio.
3. Open `gabriel-android-common/client/Tasks/upload`.
4. Run uploadArchives
5. Snapshots will be published to
   https://oss.sonatype.org/content/repositories/snapshots/edu/cmu/cs/gabriel/client/.
6. Relases should show up at https://oss.sonatype.org/#stagingRepositories.
   1. Publish a release by first clicking the `Close` button. Then click the
      `Release` button when it becomes available.
   2. Releases will be published to
      https://repo1.maven.org/maven2/edu/cmu/cs/gabriel/client/.
