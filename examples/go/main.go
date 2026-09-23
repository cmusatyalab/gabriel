// Command main demonstrates the Go bindings.
package main

import (
	"fmt"

	gabriel "github.com/cmusatyalab/gabriel/go"
)

func main() {
	fmt.Println("gabriel version:", gabriel.Version())
	fmt.Println("2 + 3 =", gabriel.Add(2, 3))
}
