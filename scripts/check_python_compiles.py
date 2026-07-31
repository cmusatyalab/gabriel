#!/usr/bin/env python3
"""Check that all given Python files compile (syntax-valid)."""

import py_compile
import sys


def main(paths: list[str]) -> int:
    """Main function to check Python syntax."""
    failed = False
    for path in paths:
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            print(e, file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
