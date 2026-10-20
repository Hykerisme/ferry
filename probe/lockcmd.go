package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
)

// runLock holds the build lock until stdin closes. Ferry keeps the SSH
// session open for the whole run, so concurrent runs are rejected before
// they can overwrite the workspace being built.
func runLock(args []string, in io.Reader, out io.Writer, errOut io.Writer) int {
	flags := flag.NewFlagSet("lock", flag.ContinueOnError)
	flags.SetOutput(errOut)
	path := flags.String("path", "", "lock file path")
	if err := flags.Parse(args); err != nil {
		return exitUsage
	}
	if *path == "" {
		fmt.Fprintln(errOut, "ferry-probe: lock requires --path")
		return exitUsage
	}
	lock, err := acquireLock(*path)
	if err != nil {
		_ = json.NewEncoder(out).Encode(map[string]any{
			"ok":      false,
			"message": "build lock is held by another run",
		})
		return exitLocked
	}
	defer lock.release()
	_ = json.NewEncoder(out).Encode(map[string]any{"ok": true, "pid": os.Getpid()})
	_, _ = io.Copy(io.Discard, in)
	return exitOK
}
