# Gabriel Android Library

## Usage

Add the lines `implementation 'edu.cmu.cs.gabriel:client:2.3.0'` and
`implementation 'edu.cmu.cs.gabriel:protocol:2.0.1'` to your app's build.gradle
file.
Your project must include either the `jcenter()` repository or the
`mavenCentral()` repository.

Your app must allow cleartext traffic. If your app does not have an Android
Network Security Config, you must specify `android:usesCleartextTraffic="true"`
in the
[application element](https://developer.android.com/guide/topics/manifest/application-element)
of your Manifest file.
If your app has an Android Network Security Config, you must allow cleartext
traffic using this
config. See
[here](https://developer.android.com/guide/topics/manifest/application-element#usesCleartextTraffic)
for more details.

Create an instance of `edu.cmu.cs.gabriel.client.comm.ServerComm` using the
static `ServerComm#createServerComm` method.
[Example](https://github.com/cmusatyalab/gabriel/blob/d93e6216f4e7f508fe2a288565ea829c45619c3f/examples/round_trip/android-client/app/src/main/java/edu/cmu/cs/roundtrip/GabrielActivity.java#L59).
This method takes one `Consumer` instance `resultConsumer` that gets called
whenever a new result is available from the server, and a second `Consumer`
instance `onDisconnect` that gets called when there is a connection problem.
This `onDisconnect` consumer should start the process of presenting an error
message to the user, cleaning up the app state, and then bringing the user back
to a screen to modify connection settings.

Send messages to the server using `ServerComm`'s `send` or `sendSupplier` methods.
[Example](https://github.com/cmusatyalab/gabriel/blob/d93e6216f4e7f508fe2a288565ea829c45619c3f/examples/round_trip/android-client/app/src/main/java/edu/cmu/cs/roundtrip/GabrielActivity.java#L69).

### Measurement

This library includes instrumentation to measure the average RTT and FPS. These
results can be sent using
[Log](https://developer.android.com/reference/android/util/Log) or written to a
file. You can also implement your own `Consumer<SourceRttFps>` that writes
results to a database and logs additional information.

I recommend creating a separate
[product flavor](https://developer.android.com/studio/build/build-variants#product-flavors)
for measurements. Create an instance of `MeasurementServerComm` instead of
`ServerComm`. Pass an instance of `LogMeasurementConsumer` as the
`intervalReporter` argument to
`MeasurementServerComm#createMeasurementServerComm`, to send measurements to log
output. Use `CsvMeasurementConsumer` to write measurements to a file.
[Example](https://github.com/cmusatyalab/openrtist/blob/dbfab2399d017b9f5ee29054dea8616dfac3ab5a/android-client/app/src/measurement/java/edu/cmu/cs/gabriel/network/MeasurementComm.java#L27)

### Camera

Add the line `implementation 'edu.cmu.cs.gabriel:camera:2.3.0'` to your app's
build.gradle file. See
[this](https://github.com/cmusatyalab/gabriel/blob/d93e6216f4e7f508fe2a288565ea829c45619c3f/examples/one_way_yuv/android-client/app/src/main/java/edu/cmu/cs/roundtrip/GabrielActivity.java#L57)
example client.

## Publishing Changes to Maven Central

1. Update the VERSION_NAME parameter in `gradle.properties`. You can add
   `-SNAPSHOT` to the end of the version number to get a snapshot published
   instead.
2. Open the `Gradle` tab in the top right of Android studio.
3. Open `gabriel-android-common/client/Tasks/upload`.
4. Run uploadArchives
5. Snapshots will be published to
   https://oss.sonatype.org/content/repositories/snapshots/edu/cmu/cs/gabriel/client/.
6. Releases should show up at https://oss.sonatype.org/#stagingRepositories.
   1. Publish a release by first clicking the `Close` button. Then click the
      `Release` button when it becomes available.
   2. Releases will be published to
      https://repo1.maven.org/maven2/edu/cmu/cs/gabriel/client/.
