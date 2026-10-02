//! Generates the C header for the public ABI (see `src/ffi.rs`).

use std::env;
use std::path::PathBuf;

const HEADER_PATH: &str = "include/gabriel/gabriel.h";

fn main() {
    let crate_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("set by cargo"));
    let config = cbindgen::Config::from_root_or_default(&crate_dir);

    cbindgen::Builder::new()
        .with_crate(&crate_dir)
        .with_config(config)
        .generate()
        .expect("failed to generate C bindings")
        .write_to_file(crate_dir.join(HEADER_PATH));

    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=cbindgen.toml");
}
