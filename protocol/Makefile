PROTO_DIR := protos
GO_OUT := go
GO_MODULE := github.com/cmusatyalab/gabriel/protocol/go
PY_OUT := python/src
PYTHON := python3

PROTO_FILES := $(shell find $(PROTO_DIR) -name '*.proto')

.PHONY: gen

gen:
	protoc/bin/protoc \
		-I $(PROTO_DIR) \
		-I protoc/include \
		--go-grpc_out=$(GO_OUT) \
		--go_out=$(GO_OUT) \
		--go_opt=module=$(GO_MODULE) \
		--go-grpc_opt=module=$(GO_MODULE) \
		$(PROTO_FILES)
	$(PYTHON) -m grpc_tools.protoc \
		-I $(PROTO_DIR) \
		--python_out=$(PY_OUT) \
		--pyi_out=$(PY_OUT) \
		--grpc_python_out=$(PY_OUT) \
		$(PROTO_FILES)

clean:
	find $(GO_OUT) -name "*pb.go" -exec rm {} \;
	find $(PY_OUT) -name "*_pb2.py" -exec rm {} \;
	find $(PY_OUT) -name "*pb2_grpc.py" -exec rm {} \;
	find $(PY_OUT) -name "*_pb2.pyi" -exec rm {} \;
