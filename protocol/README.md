# Gabriel Protocol

See `gabriel.proto` for protocol definitions.

## Compiling for Python

You must use protoc version 3.12 or newer. You can download protoc for Linux
from
[here](https://github.com/protocolbuffers/protobuf/releases/download/v3.12.3/protoc-3.12.3-linux-x86_64.zip).
You can find links to download protoc for other platforms on
[this page](https://github.com/protocolbuffers/protobuf/releases). Just make
sure the archive you download begins with "protoc."

After you have extracted the compiler, run the following line from this
directory:
`/path/to/protoc --python_out=python/src/gabriel_protocol/ gabriel.proto`

## Compiling for Java

1. Open the project in the `java` directory with Android studio.
2. Click the `Gradle` button in the top right.
3. Select `java` > `Tasks` > `build` > `build`. Do not select any of the build
   tasks specific to any of the modules (such as `protocol`).

## Publishing Changes to PyPi

Update the version number in `python/setup.py`. Then follow
[these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).

## Publishing Changes to Maven Central

Run the `uploadArchives` Gradle task from `java` > `Tasks` > `upload`. See
[these instructions](https://github.com/cmusatyalab/gabriel/blob/master/android-client/README.md#publishing-changes-to-maven-central)
for more details.
