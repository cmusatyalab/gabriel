//! Generates the Rust code of the messages in `proto/`, with protobuf's pure Rust parser, so the
//! build needs no protoc.

fn main() {
    protobuf_codegen::Codegen::new()
        .pure()
        .include("proto")
        .input("proto/h264.proto")
        .cargo_out_dir("proto")
        .run_from_script();
}
