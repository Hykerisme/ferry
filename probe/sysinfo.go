package main

import (
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

type NodeInfo struct {
	OS       string `json:"os"`
	Arch     string `json:"arch"`
	Kernel   string `json:"kernel"`
	Distro   string `json:"distro"`
	OSVer    string `json:"os_ver"`
	Init     string `json:"init"`
	Glibc    string `json:"glibc"`
	HasTar   bool   `json:"has_tar"`
	HasGzip  bool   `json:"has_gzip"`
	HasRsync bool   `json:"has_rsync"`
	HasScp   bool   `json:"has_scp"`
}

func printNodeInfo(w io.Writer) error {
	info := collectNodeInfo()
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return enc.Encode(info)
}

func collectNodeInfo() NodeInfo {
	info := NodeInfo{
		OS:       runtime.GOOS,
		Arch:     runtime.GOARCH,
		HasTar:   hasCommand("tar"),
		HasGzip:  hasCommand("gzip"),
		HasRsync: hasCommand("rsync"),
		HasScp:   hasCommand("scp"),
	}
	if data, err := os.ReadFile("/proc/sys/kernel/osrelease"); err == nil {
		info.Kernel = strings.TrimSpace(string(data))
	}
	release := readOSRelease()
	info.Distro = release["ID"]
	info.OSVer = release["VERSION_ID"]
	info.Init = detectInit()
	info.Glibc = detectGlibc()
	return info
}

func hasCommand(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

func readOSRelease() map[string]string {
	result := make(map[string]string)
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return result
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		result[key] = strings.Trim(value, `"'`)
	}
	return result
}

func detectInit() string {
	if _, err := os.Stat("/run/systemd/system"); err == nil {
		return "systemd"
	}
	if data, err := os.ReadFile("/proc/1/comm"); err == nil {
		switch strings.TrimSpace(string(data)) {
		case "systemd":
			return "systemd"
		case "init":
			return "sysvinit"
		}
	}
	if _, err := os.Lstat("/sbin/init"); err == nil {
		return "sysvinit"
	}
	return "none"
}

var glibcRe = regexp.MustCompile(`(\d+\.\d+(?:\.\d+)?)`)

func detectGlibc() string {
	ldd := "ldd"
	if _, err := filepath.Abs("/usr/bin/ldd"); err == nil {
		if _, statErr := os.Stat("/usr/bin/ldd"); statErr == nil {
			ldd = "/usr/bin/ldd"
		}
	}
	out, err := exec.Command(ldd, "--version").Output()
	if err != nil {
		return ""
	}
	firstLine := strings.SplitN(string(out), "\n", 2)[0]
	lower := strings.ToLower(firstLine)
	if !strings.Contains(lower, "glibc") && !strings.Contains(lower, "gnu libc") {
		return ""
	}
	if match := glibcRe.FindString(firstLine); match != "" {
		return match
	}
	return ""
}
