# Gabriel for Python

Python bindings for Gabriel, through its C ABI (`rust/include/gabriel/gabriel.h`), with
[cffi](https://cffi.readthedocs.io).

The package is built with [maturin](https://www.maturin.rs), which compiles the Rust crate and
puts the library and its cffi declarations in the package, as `gabriel._native`. uv builds it
when it installs the package, and again when the crate's sources change:

```sh
cd python && uv run pytest            # builds the package, then runs the tests
```

## Wheels for a device

`Dockerfile.aarch64` cross-builds a wheel for an aarch64 device with Ubuntu 20.04, such as a
Particle Tachyon. From the repository root:

```sh
docker build -t gabriel-wheel-aarch64 -f python/Dockerfile.aarch64 python
docker run --rm -u $(id -u):$(id -g) -v $PWD:/src -w /src/python \
    -e CARGO_TARGET_DIR=/src/target/aarch64 gabriel-wheel-aarch64 \
    uv build -o /src/target/wheels
```

The wheel is in `target/wheels/`. uv builds it from the sdist, which has only the files git
tracks. It talks to other Gabriel processes, such as a Go app linked
with `libgabriel.a`, over shared memory, whose layout the ABI version does not cover: build both
from the same commit.
