package gabriel

import "testing"

func TestLightningVersion(t *testing.T) {
	if LightningVersion() == "" {
		t.Fatal("LightningVersion() returned an empty string")
	}
}
