module github.com/cmusatyalab/gabriel/go-client

go 1.25.5

require github.com/cmusatyalab/gabriel/protocol/go v0.1.0

require (
	golang.org/x/sync v0.20.0
	google.golang.org/protobuf v1.36.11
)

require (
	github.com/rs/zerolog v1.35.1
	google.golang.org/grpc v1.82.0
)

require (
	github.com/mattn/go-colorable v0.1.14 // indirect
	github.com/mattn/go-isatty v0.0.20 // indirect
	golang.org/x/net v0.53.0 // indirect
	golang.org/x/sys v0.43.0 // indirect
	golang.org/x/text v0.36.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260414002931-afd174a4e478 // indirect
)

replace github.com/cmusatyalab/gabriel/protocol/go => ../protocol/go
