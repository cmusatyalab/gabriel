# Gabriel for Python

Python bindings for Gabriel, through its C ABI (`rust/include/gabriel/gabriel.h`), with
[cffi](https://cffi.readthedocs.io).

```sh
cargo build --release -p gabriel     # builds libgabriel.so and gabriel_cffi.h
cd python && uv run pytest            # tests
```

The package loads `target/release/libgabriel.so` from this repository, or the library that
`GABRIEL_LIBRARY` names.
