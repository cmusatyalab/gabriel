// This file lets cgo compile the canonical gabriel C sources directly
// as part of `go build`, so consumers never need a prebuilt .so/.a.
// The single source of truth stays in ../c/src; nothing is duplicated.
//
// One shim per source file (see also csrc_lightning.c): cgo only
// compiles .c files that live in this package directory, and keeping
// each #include in its own file preserves normal one-translation-
// unit-per-file compilation instead of merging everything into a
// single unity build, which would risk silent symbol collisions.
#include "../c/src/gabriel.c"
