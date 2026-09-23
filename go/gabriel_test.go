package gabriel

import "testing"

func TestAdd(t *testing.T) {
	if got := Add(2, 3); got != 5 {
		t.Fatalf("Add(2, 3) = %d, want 5", got)
	}
}

func TestVersion(t *testing.T) {
	if Version() == "" {
		t.Fatal("Version() returned an empty string")
	}
}
