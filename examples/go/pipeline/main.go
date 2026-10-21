// Command pipeline runs a small Gabriel pipeline on this machine, all in one process.
//
// A camera makes 30 frames a second of a bright square moving over a dark background. A motion
// filter passes a frame on only if it differs enough from the last frame it passed, and a detector
// finds the square in each frame it gets. The app prints each detection, and how old the newest
// detection is (its age of information).
//
//	go generate github.com/cmusatyalab/gabriel/go   # builds the Rust library, once
//	go run ./examples/go/pipeline
//
// In a real deployment the server, each engine, and the app are separate processes; here they are
// goroutines so the example runs with one command.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os/signal"
	"sync"
	"syscall"
	"time"

	gabriel "github.com/cmusatyalab/gabriel/go"
)

// The names of the parts of the pipeline. They are global on this machine.
const (
	serverName   = "example-server"
	clientName   = "example-client"
	cameraName   = "example-camera"
	filterName   = "example-filter"
	detectorName = "example-detector"
	flowName     = "detection"
)

// The camera's frames: 8-bit grey, with a square moving across them.
const (
	frameWidth    = 320
	frameHeight   = 240
	frameSize     = frameWidth * frameHeight
	framesPerSec  = 30
	squareSide    = 40
	squareStep    = 2 // pixels per frame
	squareBright  = 255
	backgroundLum = 20
)

const (
	// motionThreshold is the mean change per pixel, over 0-255, above which the filter passes a
	// frame.
	motionThreshold = 0.5
	// brightThreshold is the level above which the detector counts a pixel as part of the square.
	brightThreshold = 128
	// detectorWork stands in for a heavier model, so the detector is the slow stage.
	detectorWork = 50 * time.Millisecond
	// maxResultSize bounds an engine's result.
	maxResultSize = 256
	// resultType names how to read the detector's results.
	resultType = "example.Box/json"
)

// flow sends every frame to the filter, and what the filter passes to the detector. Each edge's
// timeout bounds how long a frame waits for its engine. The camera's tokens bound how many of its
// frames are in the flow at once: one per engine and per edge into it, plus one ("Choosing C" in
// the design note).
var flow = fmt.Sprintf(`
[[producers]]
name = %[1]q
tokens = 5

[[engines]]
name = %[2]q
location = "local"

[[engines]]
name = %[3]q
location = "local"

[[edges]]
from = %[1]q
to = %[2]q
timeout = "100ms"

[[edges]]
from = %[2]q
to = %[3]q
timeout = "500ms"
`, cameraName, filterName, detectorName)

// published holds when the camera published each frame, by sequence number, so the app can tell
// how old a frame is when its detection arrives.
var published sync.Map

// Box is where the detector found the square, in pixels.
type Box struct {
	X, Y, Width, Height int
}

func main() {
	duration := flag.Duration("duration", 5*time.Second, "how long to run")
	flag.Parse()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, *duration)
	defer cancel()
	if err := run(ctx); err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context) error {
	server, err := gabriel.NewServer(serverName)
	if err != nil {
		return err
	}
	defer server.Close()

	// Each engine runs in a goroutine of its own, and must exist before the server serves it.
	done := make(chan error, 2)
	for _, engine := range []struct {
		name    string
		process func(*gabriel.Engine, gabriel.Input) error
	}{
		{filterName, newMotionFilter().process},
		{detectorName, detect},
	} {
		ready := make(chan error)
		go func() { done <- serve(ctx, engine.name, engine.process, ready) }()
		if err := <-ready; err != nil {
			return err
		}
		if err := server.AddEngine(engine.name); err != nil {
			return err
		}
	}

	client, err := gabriel.NewClient(clientName, serverName)
	if err != nil {
		return err
	}
	defer client.Close()
	// The camera and the subscription must exist before the client takes the camera's inputs and
	// the flow starts.
	ready := make(chan error)
	go func() { done <- watch(ctx, ready) }()
	if err := <-ready; err != nil {
		return err
	}
	go func() { done <- film(ctx, ready) }()
	if err := <-ready; err != nil {
		return err
	}
	if err := client.AddProducer(cameraName); err != nil {
		return err
	}
	if err := client.AddFlow(flowName, flow); err != nil {
		return err
	}

	<-ctx.Done()
	var errs []error
	for range 4 {
		errs = append(errs, <-done)
	}
	return errors.Join(errs...)
}

// serve runs the engine name until ctx is done, giving process each input. It reports on ready
// once the engine exists.
func serve(ctx context.Context, name string, process func(*gabriel.Engine, gabriel.Input) error, ready chan<- error) error {
	engine, err := gabriel.NewEngine(name, resultType, maxResultSize)
	ready <- err
	if err != nil {
		return err
	}
	defer engine.Close()
	for {
		input, err := engine.Next(ctx)
		if err != nil {
			return ignoreDone(ctx, err)
		}
		if err := process(engine, input); err != nil {
			return err
		}
	}
}

// motionFilter passes a frame on if it differs enough from the last frame it passed.
type motionFilter struct {
	last []byte
}

func newMotionFilter() *motionFilter {
	return &motionFilter{}
}

func (f *motionFilter) process(engine *gabriel.Engine, input gabriel.Input) error {
	if f.last != nil && meanChange(f.last, input.Data) < motionThreshold {
		return engine.Drop(nil)
	}
	// The input's bytes are valid only until the outcome, so keep a copy.
	f.last = append(f.last[:0], input.Data...)
	return engine.Forward(nil)
}

func meanChange(a, b []byte) float64 {
	if len(a) != len(b) {
		return float64(squareBright)
	}
	var total int
	for i := range a {
		total += max(int(a[i])-int(b[i]), int(b[i])-int(a[i]))
	}
	return float64(total) / float64(len(a))
}

// detect finds the bright square, and reports its box as the result.
func detect(engine *gabriel.Engine, input gabriel.Input) error {
	time.Sleep(detectorWork)
	box := Box{X: frameWidth, Y: frameHeight}
	right, bottom := -1, -1
	for i, level := range input.Data {
		if level < brightThreshold {
			continue
		}
		x, y := i%frameWidth, i/frameWidth
		box.X, box.Y = min(box.X, x), min(box.Y, y)
		right, bottom = max(right, x), max(bottom, y)
	}
	if right < 0 {
		return engine.Drop(nil)
	}
	box.Width, box.Height = right-box.X+1, bottom-box.Y+1
	result, err := json.Marshal(box)
	if err != nil {
		return err
	}
	return engine.Drop(result)
}

// film publishes frames from the camera until ctx is done. It reports on ready once the camera
// exists.
func film(ctx context.Context, ready chan<- error) error {
	camera, err := gabriel.NewProducer(cameraName, "", frameSize)
	ready <- err
	if err != nil {
		return err
	}
	defer camera.Close()
	ticker := time.NewTicker(time.Second / framesPerSec)
	defer ticker.Stop()
	for frame := 0; ; frame++ {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
		// Draw the frame straight into shared memory.
		buf, err := camera.Loan()
		if err != nil {
			return err
		}
		draw(buf[:frameSize], frame)
		at := gabriel.Now()
		seq, err := camera.Publish(flowName, frameSize)
		if err != nil {
			return err
		}
		published.Store(seq, at)
	}
}

// draw draws the square where it is in frame number n: moving right, and wrapping around.
func draw(buf []byte, n int) {
	for i := range buf {
		buf[i] = backgroundLum
	}
	left := (n * squareStep) % (frameWidth - squareSide)
	top := (frameHeight - squareSide) / 2
	for y := top; y < top+squareSide; y++ {
		for x := left; x < left+squareSide; x++ {
			buf[y*frameWidth+x] = squareBright
		}
	}
}

// watch prints what happens to the camera's frames until ctx is done. It reports on ready once it
// is subscribed.
func watch(ctx context.Context, ready chan<- error) error {
	events, err := gabriel.SubscribeProducer(cameraName)
	ready <- err
	if err != nil {
		return err
	}
	defer events.Close()
	counts := map[string]int{}
	defer func() { fmt.Println("per engine and kind:", counts) }()
	for {
		event, err := events.Next(ctx)
		if err != nil {
			return ignoreDone(ctx, err)
		}
		counts[event.Engine+" "+event.Kind.String()]++
		if event.Kind != gabriel.EventResult || event.Engine != detectorName || len(event.Result) == 0 {
			continue
		}
		var box Box
		if err := json.Unmarshal(event.Result, &box); err != nil {
			return err
		}
		at, ok := published.Load(event.ID.Seq)
		if !ok {
			continue
		}
		// Frames before this one get no detection any more.
		published.Range(func(seq, _ any) bool {
			if seq.(uint64) <= event.ID.Seq {
				published.Delete(seq)
			}
			return true
		})
		fmt.Printf("frame %4d: square at (%3d, %3d), frame %v old\n",
			event.ID.Seq, box.X, box.Y, gabriel.Now().Sub(at.(gabriel.Timestamp)).Round(time.Millisecond))
	}
}

// ignoreDone drops the error a call returns because ctx is done.
func ignoreDone(ctx context.Context, err error) error {
	if ctx.Err() != nil && errors.Is(err, ctx.Err()) {
		return nil
	}
	return err
}
