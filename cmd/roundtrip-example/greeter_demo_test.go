package main

import "testing"

func TestRunGreeterDemo(t *testing.T) {
	resp, err := runGreeterDemo("world")
	if err != nil {
		t.Fatalf("runGreeterDemo returned error: %v", err)
	}
	if resp.Id() != 42 {
		t.Errorf("Id() = %d, want 42", resp.Id())
	}
	if got, want := string(resp.Message()), "hello, world"; got != want {
		t.Errorf("Message() = %q, want %q", got, want)
	}
}
