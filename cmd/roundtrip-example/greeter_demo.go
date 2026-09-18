package main

import (
	"context"
	"fmt"
	"net"

	"github.com/cmusatyalab/gabriel/api/go/fbs"
	flatbuffers "github.com/google/flatbuffers/go"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/encoding"
)

// greeterServer implements the flatc-generated fbs.GreeterServer interface.
type greeterServer struct {
	fbs.UnimplementedGreeterServer
}

func (greeterServer) Greet(_ context.Context, in *fbs.Greeting) (*flatbuffers.Builder, error) {
	b := flatbuffers.NewBuilder(0)
	message := b.CreateString("hello, " + string(in.Message()))
	fbs.GreetingStart(b)
	fbs.GreetingAddId(b, in.Id()+1)
	fbs.GreetingAddMessage(b, message)
	b.Finish(fbs.GreetingEnd(b))
	return b, nil
}

// runGreeterDemo starts a Greeter gRPC server on a local listener, calls it
// with the flatc-generated client, and returns the round-tripped response.
// This exercises the generated gRPC code itself, not just the plain
// FlatBuffers bindings that api/go/fbs/Greeting.go already covers.
func runGreeterDemo(name string) (*fbs.Greeting, error) {
	// FlatbuffersCodec ships with the flatbuffers Go runtime; it must be
	// registered once so grpc-go knows how to (de)serialize the
	// *flatbuffers.Builder / *fbs.Greeting types the generated stubs use.
	encoding.RegisterCodec(flatbuffers.FlatbuffersCodec{})

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen: %w", err)
	}
	defer lis.Close()

	server := grpc.NewServer()
	fbs.RegisterGreeterServer(server, greeterServer{})
	go server.Serve(lis) //nolint:errcheck // demo server, stopped below
	defer server.Stop()

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	req := flatbuffers.NewBuilder(0)
	reqMessage := req.CreateString(name)
	fbs.GreetingStart(req)
	fbs.GreetingAddId(req, 41)
	fbs.GreetingAddMessage(req, reqMessage)
	req.Finish(fbs.GreetingEnd(req))

	client := fbs.NewGreeterClient(conn)
	resp, err := client.Greet(context.Background(), req, grpc.CallContentSubtype(flatbuffers.Codec))
	if err != nil {
		return nil, fmt.Errorf("greet: %w", err)
	}
	return resp, nil
}
