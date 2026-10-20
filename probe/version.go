package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
)

var (
	version   = "0.1.0"
	buildHash = "dev"
)

type versionInfo struct {
	OK      bool   `json:"ok"`
	Version string `json:"version"`
	Hash    string `json:"hash"`
	OS      string `json:"os"`
	Arch    string `json:"arch"`
}

func printVersion() {
	enc := json.NewEncoder(os.Stdout)
	_ = enc.Encode(versionInfo{
		OK:      true,
		Version: version,
		Hash:    buildHash,
		OS:      runtime.GOOS,
		Arch:    runtime.GOARCH,
	})
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: ferry-probe <version|probe|exec|lock|put|get>")
}
