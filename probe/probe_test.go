package main

import (
	"archive/tar"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func makeTar(t *testing.T, entries map[string]string) *bytes.Buffer {
	t.Helper()
	buffer := &bytes.Buffer{}
	writer := tar.NewWriter(buffer)
	for name, content := range entries {
		header := &tar.Header{Name: name, Mode: 0o644, Size: int64(len(content))}
		if err := writer.WriteHeader(header); err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer
}

func TestExtractTarBasic(t *testing.T) {
	dest := t.TempDir()
	files, size, err := extractTar(makeTar(t, map[string]string{"a/b.txt": "hello"}), dest)
	if err != nil {
		t.Fatal(err)
	}
	if files != 1 || size != 5 {
		t.Fatalf("unexpected counts: files=%d size=%d", files, size)
	}
	data, err := os.ReadFile(filepath.Join(dest, "a", "b.txt"))
	if err != nil || string(data) != "hello" {
		t.Fatalf("unexpected content: %q err=%v", data, err)
	}
}

func TestExtractTarRejectsTraversal(t *testing.T) {
	dest := t.TempDir()
	outside := filepath.Join(filepath.Dir(dest), "escaped.txt")
	_ = os.Remove(outside)
	_, _, err := extractTar(makeTar(t, map[string]string{"../escaped.txt": "bad"}), dest)
	if err != nil {
		t.Fatal("traversal entry should be normalized inside destination, got error:", err)
	}
	if _, statErr := os.Stat(outside); statErr == nil {
		t.Fatal("entry escaped the destination")
	}
}

func TestExtractTarRejectsAbsoluteSymlink(t *testing.T) {
	buffer := &bytes.Buffer{}
	writer := tar.NewWriter(buffer)
	_ = writer.WriteHeader(&tar.Header{
		Name:     "link",
		Typeflag: tar.TypeSymlink,
		Linkname: "/etc/passwd",
	})
	_ = writer.Close()
	if _, _, err := extractTar(buffer, t.TempDir()); err == nil {
		t.Fatal("absolute symlink must be rejected")
	}
}

func TestParseResult(t *testing.T) {
	cases := []struct {
		data      []byte
		state     string
		text      string
		truncated bool
	}{
		{nil, "empty", "", false},
		{[]byte("ok=1\nignored"), "value", "ok=1", false},
		{[]byte(strings.Repeat("x", 600)), "value", strings.Repeat("x", 512), true},
	}
	for _, test := range cases {
		result := parseResult(test.data, true)
		if result.State != test.state || result.Text != test.text || result.Truncated != test.truncated {
			t.Fatalf("unexpected result for %q: %+v", test.data, result)
		}
	}
}

func TestReadLocalResultMissingIsUnset(t *testing.T) {
	result := readLocalResult(filepath.Join(t.TempDir(), "missing"))
	if result.State != "unset" {
		t.Fatalf("expected unset, got %+v", result)
	}
}

func TestMarkerLifecycle(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "marker.json")
	if err := writeMarker(path, "run-1"); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		t.Fatal(err)
	}
	if payload["run_id"] != "run-1" {
		t.Fatalf("unexpected marker: %v", payload)
	}
}

func TestTailBufferKeepsLastLines(t *testing.T) {
	tail := &tailBuffer{limit: 2}
	tail.add("a")
	tail.add("b")
	tail.add("c")
	if got := tail.string(); got != "b\nc" {
		t.Fatalf("unexpected tail: %q", got)
	}
}

func shellCommand(command string) []string {
	if runtime.GOOS == "windows" {
		return []string{"cmd", "/c", command}
	}
	return []string{"sh", "-c", command}
}

func TestRunExecReportsStepEvents(t *testing.T) {
	request := ExecRequest{
		V:       1,
		RunID:   "test",
		Workdir: t.TempDir(),
		Steps: []Step{
			{ID: "one", Argv: shellCommand("echo hello"), TimeoutMS: 10000},
		},
	}
	payload, _ := json.Marshal(request)
	out := &bytes.Buffer{}
	errOut := &bytes.Buffer{}
	code := runExec(bytes.NewReader(payload), out, errOut)
	if code != 0 {
		t.Fatalf("expected success, got %d: %s", code, errOut.String())
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) < 3 {
		t.Fatalf("expected events, got %q", out.String())
	}
	var sawStart, sawEnd bool
	for _, line := range lines {
		var event map[string]any
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatalf("invalid event %q: %v", line, err)
		}
		if event["event"] == "start" {
			sawStart = true
		}
		if event["event"] == "end" {
			sawEnd = true
		}
	}
	if !sawStart || !sawEnd {
		t.Fatalf("missing lifecycle events: %q", out.String())
	}
}

func TestRunExecLockBusy(t *testing.T) {
	dir := t.TempDir()
	lockPath := filepath.Join(dir, "build.lock")
	lock, err := acquireLock(lockPath)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.release()
	request := ExecRequest{V: 1, RunID: "test", Lock: &LockSpec{Path: lockPath}}
	payload, _ := json.Marshal(request)
	out := &bytes.Buffer{}
	code := runExec(bytes.NewReader(payload), out, &bytes.Buffer{})
	if code != exitLocked {
		t.Fatalf("expected lock failure %d, got %d", exitLocked, code)
	}
	if !strings.Contains(out.String(), "lock") {
		t.Fatalf("expected lock event, got %q", out.String())
	}
}

func TestRunExecResultFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell result-file test requires POSIX sh")
	}
	command := `printf 'key=value\nignored\n' > "$FERRY_RESULT_FILE"`
	request := ExecRequest{
		V:       1,
		RunID:   "test",
		Workdir: t.TempDir(),
		Steps: []Step{
			{ID: "result", Argv: []string{"sh", "-c", command}, Result: true},
		},
	}
	payload, _ := json.Marshal(request)
	out := &bytes.Buffer{}
	code := runExec(bytes.NewReader(payload), out, &bytes.Buffer{})
	if code != 0 {
		t.Fatalf("expected success, got %d", code)
	}
	if !strings.Contains(out.String(), `"state":"value"`) && !strings.Contains(out.String(), `"state": "value"`) {
		t.Fatalf("expected result state, got %q", out.String())
	}
}

func TestRunExecBuildScript(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("build script execution requires POSIX sh")
	}
	content := "#!/bin/sh\nprintf 'script-ok\\n'\nprintf 'val=1\\n' > \"$FERRY_RESULT_FILE\"\n"
	request := ExecRequest{
		V:       1,
		RunID:   "test",
		Workdir: t.TempDir(),
		Steps: []Step{
			{
				ID:        "script",
				Where:     "build",
				Kind:      "script",
				ScriptB64: base64.StdEncoding.EncodeToString([]byte(content)),
				Cwd:       t.TempDir(),
				Result:    true,
			},
		},
	}
	payload, _ := json.Marshal(request)
	out := &bytes.Buffer{}
	code := runExec(bytes.NewReader(payload), out, &bytes.Buffer{})
	if code != 0 {
		t.Fatalf("expected success, got %d: %s", code, out.String())
	}
	if !strings.Contains(out.String(), base64.StdEncoding.EncodeToString([]byte("script-ok"))) {
		t.Fatalf("expected script output, got %q", out.String())
	}
	if !strings.Contains(out.String(), `"text":"val=1"`) && !strings.Contains(out.String(), `"text": "val=1"`) {
		t.Fatalf("expected script result, got %q", out.String())
	}
}
