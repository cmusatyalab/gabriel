//! Generates the C header for the public ABI (see `src/ffi.rs`), and a copy of it for cffi.
//!
//! cffi reads declarations and `#define`s of constants, but no other preprocessor lines, such as
//! the `#if`s that cbindgen writes around enums of a fixed width. So its copy is the header after
//! the C preprocessor, with the constants put back.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const HEADER_PATH: &str = "include/gabriel/gabriel.h";
const CFFI_HEADER_PATH: &str = "include/gabriel/gabriel_cffi.h";
const CFFI_HEADER_NAME: &str = "gabriel_cffi.h";

fn main() {
    let crate_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("set by cargo"));
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("set by cargo"));
    let config = cbindgen::Config::from_root_or_default(&crate_dir);

    let cffi_config = cbindgen::Config {
        no_includes: true,
        sys_includes: Vec::new(),
        includes: Vec::new(),
        include_guard: None,
        cpp_compat: false,
        ..config.clone()
    };
    generate(&crate_dir, config, &crate_dir.join(HEADER_PATH));
    let unprocessed = out_dir.join(CFFI_HEADER_NAME);
    generate(&crate_dir, cffi_config, &unprocessed);
    fs::write(crate_dir.join(CFFI_HEADER_PATH), preprocess(&unprocessed))
        .expect("failed to write the cffi header");

    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=cbindgen.toml");
}

fn generate(crate_dir: &Path, config: cbindgen::Config, path: &Path) {
    cbindgen::Builder::new()
        .with_crate(crate_dir)
        .with_config(config)
        .generate()
        .expect("failed to generate C bindings")
        .write_to_file(path);
}

/// The header at `path` for cffi: its `#define`s of constants, then its declarations after the C
/// preprocessor, as C11 and without line markers (`-P`).
fn preprocess(path: &Path) -> Vec<u8> {
    let header = fs::read_to_string(path).expect("failed to read the generated header");
    let mut processed: Vec<u8> = header
        .lines()
        .filter(|line| line.starts_with("#define "))
        .flat_map(|line| [line.as_bytes(), b"\n"].concat())
        .collect();
    processed.extend(
        cc::Build::new()
            .file(path)
            .std("c11")
            .flag("-P")
            .cargo_metadata(false)
            .expand(),
    );
    processed
}
