// Command main demonstrates the Go bindings.
package main

import (
	"fmt"

	gabriel "github.com/cmusatyalab/gabriel/go"
)

func main() {
	fmt.Println("lightning version:", gabriel.LightningVersion())
}
