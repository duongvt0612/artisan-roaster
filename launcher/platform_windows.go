//go:build windows

package main

import (
	"syscall"
	"time"
	"unsafe"
)

var procMessageBoxW = syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")

const mbIconWarning = 0x30

func showMessage(title, text string) {
	t, _ := syscall.UTF16PtrFromString(title)
	m, _ := syscall.UTF16PtrFromString(text)
	_, _, _ = procMessageBoxW.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(t)), mbIconWarning)
}

// waitPID waits until the process ends (or the limit is reached); a process that is already gone returns at once.
func waitPID(pid int, limit time.Duration) {
	h, err := syscall.OpenProcess(syscall.SYNCHRONIZE, false, uint32(pid))
	if err != nil {
		return
	}
	defer func() { _ = syscall.CloseHandle(h) }()
	_, _ = syscall.WaitForSingleObject(h, uint32(limit.Milliseconds()))
}
