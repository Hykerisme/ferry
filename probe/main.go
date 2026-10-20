package main

import (
	"os"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "version":
		printVersion()
	case "probe":
		if err := printNodeInfo(os.Stdout); err != nil {
			fatal(err)
		}
	case "exec":
		os.Exit(runExec(os.Stdin, os.Stdout, os.Stderr))
	case "lock":
		os.Exit(runLock(os.Args[2:], os.Stdin, os.Stdout, os.Stderr))
	case "put":
		os.Exit(runPut(os.Args[2:], os.Stdin, os.Stdout, os.Stderr))
	case "get":
		os.Exit(runGet(os.Args[2:], os.Stdout, os.Stderr))
	default:
		usage()
		os.Exit(2)
	}
}

func fatal(err error) {
	os.Stderr.WriteString("ferry-probe: " + err.Error() + "\n")
	os.Exit(2)
}
