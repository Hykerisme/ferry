//go:build windows

package main

import (
	"os"
	"os/exec"
	"syscall"
)

func configureProcess(cmd *exec.Cmd) {}

func terminateGroup(pid int, sig syscall.Signal) {
	if pid <= 0 {
		return
	}
	process, err := os.FindProcess(pid)
	if err != nil {
		return
	}
	_ = process.Kill()
}

func trapSignals() []os.Signal {
	return []os.Signal{os.Interrupt}
}

func currentGroup() int {
	return os.Getpid()
}
