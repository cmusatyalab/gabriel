PROTO_DIR := protos
GO_OUT := go
PY_OUT := python/src/gabriel_protocol

PROTO_FILES := $(shell find $(PROTO_DIR) -name '*.proto')

.PHONY: gen

gen:
	protoc/bin/protoc \
		-I $(PROTO_DIR) \
		-I protoc/include \
		--go_out=$(GO_OUT) \
		--go_opt=paths=source_relative \
		--python_out=$(PY_OUT) \
		$(PROTO_FILES)

clean:
	find $(GO_OUT) -name "*pb.go" -exec rm {} \;
	find $(PY_OUT) -name "*_pb2.py" -exec rm {} \;
