package gabriel

import "testing"

func TestNewGreeting(t *testing.T) {
	g, err := NewGreeting(42, "hello from C++")
	if err != nil {
		t.Fatalf("NewGreeting returned error: %v", err)
	}
	if g.ID != 42 {
		t.Errorf("ID = %d, want 42", g.ID)
	}
	if g.Message != "hello from C++" {
		t.Errorf("Message = %q, want %q", g.Message, "hello from C++")
	}
}
