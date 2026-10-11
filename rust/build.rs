//! Generates the C header for the public ABI (see `src/ffi.rs`), and a copy of it for cffi, which
//! reads declarations but no preprocessor lines other than `#define`.

use std::env;
use std::path::{Path, PathBuf};

const HEADER_PATH: &str = "include/gabriel/gabriel.h";
const CFFI_HEADER_PATH: &str = "include/gabriel/gabriel_cffi.h";

fn main() {
    let crate_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("set by cargo"));
    let config = cbindgen::Config::from_root_or_default(&crate_dir);

    let cffi_config = cbindgen::Config {
        no_includes: true,
        sys_includes: Vec::new(),
        includes: Vec::new(),
        include_guard: None,
        cpp_compat: false,
        ..config.clone()
    };
    generate(&crate_dir, config, HEADER_PATH);
    generate(&crate_dir, cffi_config, CFFI_HEADER_PATH);

    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=cbindgen.toml");
}

fn generate(crate_dir: &Path, config: cbindgen::Config, path: &str) {
    cbindgen::Builder::new()
        .with_crate(crate_dir)
        .with_config(config)
        .generate()
        .expect("failed to generate C bindings")
        .write_to_file(crate_dir.join(path));
}
