//go:build !windows

package main

import (
	"fmt"
	"os"
	"syscall"
	"time"
)

// The launcher is only shipped for Windows; this keeps it building and testable elsewhere.

func showMessage(title, text string) {
	fmt.Fprintf(os.Stderr, "%s: %s\n", title, text)
}

func waitPID(pid int, limit time.Duration) {
	end := time.Now().Add(limit)
	for time.Now().Before(end) {
		if syscall.Kill(pid, 0) != nil {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
}
