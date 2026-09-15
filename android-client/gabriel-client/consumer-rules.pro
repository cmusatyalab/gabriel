# gRPC and protobuf resolve implementations reflectively; keep their
# generated code intact under minification in apps that depend on this
# library.
-keep class edu.cmu.cs.gabriel.client.** { *; }
-keep class io.grpc.** { *; }
-keep class com.google.protobuf.** { *; }
-keep class gabriel_protocol.** { *; }
