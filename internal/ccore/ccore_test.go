package ccore

import (
	"testing"

	"github.com/cmusatyalab/gabriel/api/go/fbs"
)

func TestBuildGreeting(t *testing.T) {
	buf, err := BuildGreeting(7, "ping")
	if err != nil {
		t.Fatalf("BuildGreeting returned error: %v", err)
	}
	if len(buf) == 0 {
		t.Fatal("BuildGreeting returned an empty buffer")
	}

	g := fbs.GetRootAsGreeting(buf, 0)
	if got := g.Id(); got != 7 {
		t.Errorf("Id() = %d, want 7", got)
	}
	if got := string(g.Message()); got != "ping" {
		t.Errorf("Message() = %q, want %q", got, "ping")
	}
}
