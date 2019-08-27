The Protobuf code was compiled by running the following commands from this
directory:
1. `/path/to/protoc --python_out=../server/gabriel/control/ gabriel.proto`
2. `/path/to/protoc --java_out=. gabriel.proto`.

Note that Scarlet does not use protobuf lite. I used version 3.0 of the protobuf
compiler. You can find instructions to download a binary of this version
[here](https://github.com/tensorflow/models/blob/master/research/object_detection/g3doc/installation.md#manual-protobuf-compiler-installation-and-usage).
The Protobuf compiler takes a while to build, so I recommend downloading a
binary instead of compiling it yourself.