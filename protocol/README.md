# Gabriel Protocol

Protos are defined in `proto/gabriel_protocol/v1/gabriel.proto`.

## Generating Go and Python code

Run `buf generate` from this directory to regenerate the Go and Python bindings in `go/` and
`python/src/`. This requires the [buf CLI](https://buf.build/docs/installation).

## Publishing Changes to PyPi

Update the `version` field in `python/pyproject.toml`. Then follow
[these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).
