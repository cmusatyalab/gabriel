// Command roundtrip-example demonstrates building a FlatBuffer in C++
// (via GStreamer-linked cgo code) and decoding it back in Go, then
// demonstrates the flatc-generated Greeter gRPC service by serving and
// calling it over a local connection.
package main

import (
	"fmt"
	"log"

	"github.com/cmusatyalab/gabriel"
)

func main() {
	g, err := gabriel.NewGreeting(1, "hello from C++")
	if err != nil {
		log.Fatalf("failed to build greeting: %v", err)
	}
	fmt.Printf("id=%d message=%q\n", g.ID, g.Message)

	resp, err := runGreeterDemo("world")
	if err != nil {
		log.Fatalf("grpc demo failed: %v", err)
	}
	fmt.Printf("grpc: id=%d message=%q\n", resp.Id(), resp.Message())
}
