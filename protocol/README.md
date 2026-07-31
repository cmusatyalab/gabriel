# Gabriel Protocol

Protos are defined in `protos/gabriel.proto`.

## Generating Go and Python code

Run `buf generate` from this directory to regenerate the Go and Python bindings in `go/` and
`python/src/`. This requires the [buf CLI](https://buf.build/docs/installation).

## Compiling Java changes

1. Open this directory as a project in IntelliJ or Android Studio.
2. Click the `Gradle` button in the top right.
3. Select `java` > `Tasks` > `build` > `build`. Do not select any of the build tasks specific to any
   of the modules (such as `protocol`). Running a task specific to a module will generate the protos
   in the wrong location.

## Publishing Changes to PyPi

Update the `version` field in `python/pyproject.toml`. Then follow
[these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).

## Publishing Changes to Maven Central

Run the `uploadArchives` Gradle task from `java` > `Tasks` > `upload`. See
[these instructions](https://github.com/cmusatyalab/gabriel/blob/master/android-client/README.md#publishing-changes-to-maven-central)
for more details.
