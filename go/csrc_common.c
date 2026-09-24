// These csrc_*.c files let cgo compile the canonical Lightning C
// sources directly as part of `go build`, so consumers never need a
// prebuilt .so/.a. The single source of truth stays in ../c/src;
// nothing is duplicated.
//
// One shim per source file: cgo only compiles .c files that live in
// this package directory, and keeping each #include in its own file
// preserves normal one-translation-unit-per-file compilation instead of
// merging everything into a single unity build, which would risk silent
// symbol collisions between the files' static helpers.
#include "../c/src/common.c"
