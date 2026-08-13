module github.com/cmusatyalab/gabriel/go-client

go 1.26.5

require github.com/cmusatyalab/gabriel/protocol/go v0.3.0

require (
	golang.org/x/sync v0.22.0
	google.golang.org/protobuf v1.36.11
)

require (
	github.com/google/uuid v1.6.0
	github.com/prometheus/client_golang v1.24.1
	github.com/rs/zerolog v1.35.1
	google.golang.org/grpc v1.83.0
)

require (
	github.com/beorn7/perks v1.0.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/mattn/go-colorable v0.1.15 // indirect
	github.com/mattn/go-isatty v0.0.24 // indirect
	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
	github.com/prometheus/client_model v0.6.2 // indirect
	github.com/prometheus/common v0.70.1 // indirect
	github.com/prometheus/procfs v0.21.1 // indirect
	golang.org/x/net v0.57.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.40.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260729162451-8efbd57d26e0 // indirect
)

replace github.com/cmusatyalab/gabriel/protocol/go => ../protocol/go
