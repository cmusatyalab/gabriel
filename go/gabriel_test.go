package gabriel

import "testing"

func TestVersion(t *testing.T) {
	if Version() == "" {
		t.Fatal("Version() returned an empty string")
	}
}
