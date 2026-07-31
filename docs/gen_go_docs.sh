#!/bin/sh
# Generates Markdown reference pages for the Go packages from their doc
# comments, so Sphinx can include them alongside the Python (autodoc) pages.
set -eu

cd "$(dirname "$0")"

if ! command -v gomarkdoc >/dev/null 2>&1; then
    GOBIN="$(go env GOPATH)/bin"
    go install github.com/princjef/gomarkdoc/cmd/gomarkdoc@latest
    PATH="$GOBIN:$PATH"
fi

gomarkdoc --output source/go_client.md ../go-client
