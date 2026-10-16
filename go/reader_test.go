package gabriel

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestReaderAndEngineBothGetTheInput(t *testing.T) {
	f := newFixture(t)
	reader, err := NewReader(f.names.producer)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	if err := f.client.AddFlow(flowName, chain(f.names.producer, f.names.engine)); err != nil {
		t.Fatal(err)
	}

	seq, err := f.producer.Send(flowName, []byte("frame"))
	if err != nil {
		t.Fatal(err)
	}

	input, err := reader.Next(timeout(t))
	if err != nil {
		t.Fatal(err)
	}
	want := InputID{Producer: f.names.producer, Session: f.producer.Session(), Seq: seq}
	if input.ID != want {
		t.Errorf("reader got input %+v, want %+v", input.ID, want)
	}
	if string(input.Data) != "frame" || input.Flow != flowName || input.PayloadType != inputType {
		t.Errorf("reader got %+v, want %q of type %q on %q", input, "frame", inputType, flowName)
	}
	if input.Lost != 0 {
		t.Errorf("reader lost %d inputs, want none", input.Lost)
	}
	if _, err := f.engine.Next(timeout(t)); err != nil {
		t.Fatalf("engine got no input: %v", err)
	}
}

func TestReaderReadsInputsInOrder(t *testing.T) {
	name := unique("camera")
	reader, err := NewReader(name)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	producer, err := NewProducer(name, "", maxInputSize)
	if err != nil {
		t.Fatal(err)
	}
	defer producer.Close()

	for _, data := range []string{"one", "two"} {
		if _, err := producer.Send(flowName, []byte(data)); err != nil {
			t.Fatal(err)
		}
	}

	for _, want := range []string{"one", "two"} {
		input, err := reader.Next(timeout(t))
		if err != nil {
			t.Fatal(err)
		}
		if string(input.Data) != want {
			t.Errorf("reader got %q, want %q", input.Data, want)
		}
	}
	if _, err := reader.Poll(); !errors.Is(err, ErrAgain) {
		t.Fatalf("got %v, want ErrAgain", err)
	}
}

func TestReaderWithoutInputTimesOut(t *testing.T) {
	reader, err := NewReader(unique("camera"))
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()

	_, err = reader.Next(ctx)

	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v, want the context's deadline", err)
	}
}

func TestReaderUsedFromAnotherGoroutineFails(t *testing.T) {
	reader, err := NewReader(unique("camera"))
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()

	errs := make(chan error)
	go func() {
		_, err := reader.Poll()
		errs <- err
	}()

	if err := <-errs; !errors.Is(err, ErrWrongThread) {
		t.Fatalf("got %v, want ErrWrongThread", err)
	}
}
