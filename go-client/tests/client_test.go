package test

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/cmusatyalab/gabriel/go-client"
	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"github.com/golang/glog"
)

func TestEndToEnd(t *testing.T) {
	glog.Infoln("Starting test")
	ctx := context.Background()
	producerFn := func(ctx context.Context) <-chan *gabrielpb.InputFrame {
		ch := make(chan *gabrielpb.InputFrame, 1)
		go func() {
			time.Sleep(time.Millisecond * 100)
			frame := &gabrielpb.InputFrame{
				Payload: &gabrielpb.InputFrame_StringPayload{
					StringPayload: "Hello world!",
				},
			}
			ch <- frame
		}()
		return ch
	}
	producer := client.NewInputProducer("producer-1", producerFn, []string{"0"})
	var receivedResponse atomic.Bool
	consumer := func(result *gabrielpb.Result) {
		receivedResponse.Store(true)
	}
	zmq_client, _ := client.NewZeroMQClient("ipc:///tmp/gabriel-server", []*client.InputProducer{producer}, consumer)

	glog.Infoln("Launching client")
	go zmq_client.Launch(ctx)
	time.Sleep(500 * time.Millisecond)
	if !receivedResponse.Load() {
		t.Fatal("Did not receive response")
	}
}
