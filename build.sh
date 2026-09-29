#!/usr/bin/env bash
# Configures and builds the Gabriel C library.
#
# Usage: ./build.sh [--no-examples] [--clean]
#   --no-examples  Skip the C/C++ examples (built by default).
#   --clean        Remove the build directory first.
#
# There's no C test suite yet (see c/CMakeLists.txt), so this doesn't
# build or run one - add that back here once one exists.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

BUILD_DIR=build
BUILD_EXAMPLES=ON

for arg in "$@"; do
    case "$arg" in
        --no-examples) BUILD_EXAMPLES=OFF ;;
        --clean) rm -rf "$BUILD_DIR" ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

cmake -S . -B "$BUILD_DIR" \
    -DGABRIEL_BUILD_EXAMPLES="$BUILD_EXAMPLES" \
    -DGABRIEL_BUILD_TESTS=OFF
cmake --build "$BUILD_DIR" -j
