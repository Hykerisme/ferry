package main

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	exitOK          = 0
	exitStepFailed  = 1
	exitUsage       = 2
	exitInterrupted = 3
	exitLocked      = 5
	exitTimeout     = 124
	killGrace       = 15 * time.Second
)

func runExec(in io.Reader, out io.Writer, errOut io.Writer) int {
	var req ExecRequest
	if err := json.NewDecoder(in).Decode(&req); err != nil {
		fmt.Fprintf(errOut, "ferry-probe: invalid exec request: %v\n", err)
		return exitUsage
	}
	if req.V != 1 {
		fmt.Fprintf(errOut, "ferry-probe: unsupported exec protocol version %d\n", req.V)
		return exitUsage
	}

	em := newEmitter(out)
	if req.Workdir != "" {
		_ = os.MkdirAll(req.Workdir, 0o755)
	}
	em.emit(Event{Event: "start", PID: os.Getpid(), PGID: currentGroup()})

	var lock *fileLock
	if req.Lock != nil {
		acquired, err := acquireLock(req.Lock.Path)
		if err != nil {
			em.emit(Event{Event: "lock", Code: exitLocked, Message: "build lock is held by another run"})
			em.emit(Event{Event: "end", Code: exitLocked})
			return exitLocked
		}
		lock = acquired
		defer lock.release()
		em.emit(Event{Event: "lock"})
	}

	var interrupted atomic.Bool
	var currentPgid atomic.Int64
	done := make(chan struct{})
	defer close(done)

	sigCh := make(chan os.Signal, 2)
	signal.Notify(sigCh, trapSignals()...)
	defer signal.Stop(sigCh)
	go func() {
		select {
		case <-sigCh:
		case <-done:
			return
		}
		interrupted.Store(true)
		if pgid := int(currentPgid.Load()); pgid > 0 {
			terminateGroup(pgid, syscall.SIGTERM)
			time.AfterFunc(killGrace, func() { terminateGroup(pgid, syscall.SIGKILL) })
		}
	}()

	if req.Marker != nil {
		if _, err := os.Stat(req.Marker.Path); err == nil {
			cleaned := make([]string, 0, len(req.CleanPaths))
			for _, path := range req.CleanPaths {
				if path == "" {
					continue
				}
				if err := os.RemoveAll(path); err == nil {
					cleaned = append(cleaned, path)
				}
			}
			em.emit(Event{Event: "recovery", Cleaned: cleaned})
		}
		if err := writeMarker(req.Marker.Path, req.RunID); err != nil {
			em.emit(Event{Event: "error", Message: "cannot write build marker: " + err.Error()})
		}
	}

	code := exitOK
	for _, step := range req.Steps {
		if interrupted.Load() || em.hasFailed() {
			code = exitInterrupted
			break
		}
		em.emit(Event{Event: "step_start", ID: step.ID})
		started := time.Now()
		outcome := executeStep(req, step, em, &interrupted, &currentPgid)
		em.emit(Event{
			Event:      "step_end",
			ID:         step.ID,
			Code:       outcome.code,
			DurationMS: int(time.Since(started).Milliseconds()),
			Tail:       outcome.tail,
			Result:     outcome.result,
		})
		if interrupted.Load() || em.hasFailed() {
			code = exitInterrupted
			break
		}
		if outcome.code != 0 {
			code = exitStepFailed
			break
		}
	}

	if req.Marker != nil && !interrupted.Load() {
		_ = os.Remove(req.Marker.Path)
	}
	em.emit(Event{Event: "end", Code: code})
	return code
}

type stepOutcome struct {
	code   int
	tail   string
	result *Result
}

type tailBuffer struct {
	mu    sync.Mutex
	lines []string
	limit int
}

func (t *tailBuffer) add(line string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.lines = append(t.lines, line)
	if len(t.lines) > t.limit {
		t.lines = t.lines[len(t.lines)-t.limit:]
	}
}

func (t *tailBuffer) string() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return strings.Join(t.lines, "\n")
}

func executeStep(req ExecRequest, step Step, em *emitter, interrupted *atomic.Bool, currentPgid *atomic.Int64) stepOutcome {
	tail := &tailBuffer{limit: 20}
	cwd := step.Cwd
	if cwd == "" {
		cwd = req.Workdir
	}

	var (
		cmd               *exec.Cmd
		remoteHost        string
		remoteResultPath  string
		localResultPath   string
		removeLocalResult func()
		removeScript      func()
	)
	defer func() {
		if removeLocalResult != nil {
			removeLocalResult()
		}
		if removeScript != nil {
			removeScript()
		}
	}()

	if step.Where == "target" {
		remoteHost = step.TargetHost
		if remoteHost == "" {
			remoteHost = req.TargetHost
		}
		if remoteHost == "" || step.ScriptB64 == "" {
			return stepOutcome{code: exitUsage, tail: "target step requires target_host and script content"}
		}
		content, err := base64.StdEncoding.DecodeString(step.ScriptB64)
		if err != nil {
			return stepOutcome{code: exitUsage, tail: "invalid script encoding"}
		}
		if step.Result {
			remoteResultPath = fmt.Sprintf(
				"/tmp/ferry-result-%s-%d",
				sanitizeID(step.ID),
				time.Now().UnixNano(),
			)
		}
		wrapper := targetWrapper(step, cwd, remoteResultPath)
		args := []string{"ssh"}
		args = append(args, req.TargetOptions...)
		if req.TargetKey != "" {
			args = append(args, "-i", req.TargetKey)
		}
		args = append(args,
			"-o", "BatchMode=yes",
			"-o", "ConnectTimeout=5",
			"-o", "StrictHostKeyChecking=accept-new",
			remoteHost,
			wrapper,
		)
		cmd = exec.Command(args[0], args[1:]...)
		cmd.Stdin = bytes.NewReader(content)
	} else if step.Kind == "script" && step.ScriptB64 != "" {
		content, err := base64.StdEncoding.DecodeString(step.ScriptB64)
		if err != nil {
			return stepOutcome{code: exitUsage, tail: "invalid script encoding"}
		}
		file, err := os.CreateTemp("", "ferry-script-*.sh")
		if err != nil {
			return stepOutcome{code: exitUsage, tail: "cannot create script file: " + err.Error()}
		}
		scriptPath := file.Name()
		if _, err := file.Write(content); err != nil {
			_ = file.Close()
			_ = os.Remove(scriptPath)
			return stepOutcome{code: exitUsage, tail: "cannot write script: " + err.Error()}
		}
		_ = file.Chmod(0o700)
		_ = file.Close()
		removeScript = func() { _ = os.Remove(scriptPath) }
		cmd = exec.Command("sh", scriptPath)
		cmd.Env = append(os.Environ(), envList(step.Env)...)
		if step.Result {
			resultFile, err := os.CreateTemp("", "ferry-result-*")
			if err != nil {
				return stepOutcome{code: exitUsage, tail: "cannot create result file: " + err.Error()}
			}
			localResultPath = resultFile.Name()
			_ = resultFile.Close()
			cmd.Env = append(cmd.Env, "FERRY_RESULT_FILE="+localResultPath)
			removeLocalResult = func() { _ = os.Remove(localResultPath) }
		}
	} else {
		if len(step.Argv) == 0 {
			return stepOutcome{code: exitUsage, tail: "empty argv"}
		}
		cmd = exec.Command(step.Argv[0], step.Argv[1:]...)
		cmd.Env = append(os.Environ(), envList(step.Env)...)
		if step.Result {
			file, err := os.CreateTemp("", "ferry-result-*")
			if err != nil {
				return stepOutcome{code: exitUsage, tail: "cannot create result file: " + err.Error()}
			}
			localResultPath = file.Name()
			_ = file.Close()
			cmd.Env = append(cmd.Env, "FERRY_RESULT_FILE="+localResultPath)
			removeLocalResult = func() { _ = os.Remove(localResultPath) }
		}
	}

	if cwd != "" {
		cmd.Dir = cwd
	}
	configureProcess(cmd)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return stepOutcome{code: exitUsage, tail: err.Error()}
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return stepOutcome{code: exitUsage, tail: err.Error()}
	}
	if err := cmd.Start(); err != nil {
		return stepOutcome{code: exitUsage, tail: "cannot start command: " + err.Error()}
	}
	currentPgid.Store(int64(cmd.Process.Pid))

	var wg sync.WaitGroup
	wg.Add(2)
	go streamPipe(stdout, "stdout", step.ID, em, tail, &wg)
	go streamPipe(stderr, "stderr", step.ID, em, tail, &wg)

	var timedOut atomic.Bool
	if step.TimeoutMS > 0 {
		time.AfterFunc(time.Duration(step.TimeoutMS)*time.Millisecond, func() {
			timedOut.Store(true)
			terminateGroup(cmd.Process.Pid, syscall.SIGTERM)
			time.AfterFunc(5*time.Second, func() {
				terminateGroup(cmd.Process.Pid, syscall.SIGKILL)
			})
		})
	}

	waitErr := cmd.Wait()
	wg.Wait()
	currentPgid.Store(0)

	code := 0
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) {
			code = exitErr.ExitCode()
			if code < 0 {
				code = 130
			}
		} else {
			code = exitStepFailed
		}
	}
	if timedOut.Load() {
		code = exitTimeout
	} else if interrupted.Load() && code == 0 {
		code = 130
	}

	var result *Result
	if step.Result {
		if step.Where == "target" {
			result = fetchRemoteResult(remoteHost, remoteResultPath, req.TargetKey, req.TargetOptions)
		} else {
			result = readLocalResult(localResultPath)
		}
	}
	return stepOutcome{code: code, tail: tail.string(), result: result}
}

func streamPipe(reader io.Reader, stream string, id string, em *emitter, tail *tailBuffer, wg *sync.WaitGroup) {
	defer wg.Done()
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 64*1024), 2*1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		tail.add(line)
		em.emit(Event{
			Event:  "out",
			ID:     id,
			Stream: stream,
			Chunk:  base64.StdEncoding.EncodeToString([]byte(line)),
		})
	}
}

func readLocalResult(path string) *Result {
	if path == "" {
		return &Result{State: "unset"}
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return &Result{State: "unset"}
		}
		return &Result{State: "unset"}
	}
	return parseResult(data, true)
}

func fetchRemoteResult(host, path, key string, targetOptions []string) *Result {
	if host == "" || path == "" {
		return &Result{State: "unset"}
	}
	quoted := shQuote(path)
	remote := fmt.Sprintf(
		"if [ -f %s ]; then printf 'X'; cat %s; rm -f %s; else printf 'M'; fi",
		quoted, quoted, quoted,
	)
	args := []string{"ssh"}
	args = append(args, targetOptions...)
	if key != "" {
		args = append(args, "-i", key)
	}
	args = append(args,
		"-o", "BatchMode=yes",
		"-o", "ConnectTimeout=5",
		"-o", "StrictHostKeyChecking=accept-new",
		host,
		remote,
	)
	cmd := exec.Command(args[0], args[1:]...)
	out, err := cmd.Output()
	if err != nil || len(out) == 0 {
		return &Result{State: "unset"}
	}
	if out[0] == 'M' {
		return &Result{State: "unset"}
	}
	return parseResult(out[1:], true)
}

func parseResult(data []byte, exists bool) *Result {
	if !exists {
		return &Result{State: "unset"}
	}
	if len(data) == 0 {
		return &Result{State: "empty"}
	}
	text := string(data)
	if index := strings.IndexByte(text, '\n'); index >= 0 {
		text = text[:index]
	}
	truncated := len(text) > 512
	if truncated {
		text = text[:512]
	}
	return &Result{State: "value", Text: text, Truncated: truncated}
}

func targetWrapper(step Step, cwd, resultPath string) string {
	scriptPath := "/tmp/ferry-step-" + sanitizeID(step.ID) + ".sh"
	env := map[string]string{}
	for key, value := range step.Env {
		env[key] = value
	}
	if resultPath != "" {
		env["FERRY_RESULT_FILE"] = resultPath
	}
	var inner strings.Builder
	if cwd != "" {
		inner.WriteString("cd ")
		inner.WriteString(shQuote(cwd))
		inner.WriteString(" && ")
	}
	inner.WriteString(envPrefix(env))
	inner.WriteString("sh ")
	inner.WriteString(shQuote(scriptPath))

	return strings.Join([]string{
		"cat > " + shQuote(scriptPath),
		"chmod +x " + shQuote(scriptPath),
		inner.String() + "; rc=$?; rm -f " + shQuote(scriptPath) + "; exit $rc",
	}, " && ")
}

func envPrefix(env map[string]string) string {
	keys := make([]string, 0, len(env))
	for key := range env {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var parts []string
	for _, key := range keys {
		parts = append(parts, key+"="+shQuote(env[key]))
	}
	if len(parts) == 0 {
		return ""
	}
	return strings.Join(parts, " ") + " "
}

func envList(env map[string]string) []string {
	keys := make([]string, 0, len(env))
	for key := range env {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := make([]string, 0, len(env))
	for _, key := range keys {
		result = append(result, key+"="+env[key])
	}
	return result
}

func shQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", `'\''`) + "'"
}

func sanitizeID(id string) string {
	var builder strings.Builder
	for _, char := range id {
		switch {
		case char >= 'a' && char <= 'z', char >= 'A' && char <= 'Z', char >= '0' && char <= '9', char == '-', char == '_':
			builder.WriteRune(char)
		default:
			builder.WriteRune('_')
		}
	}
	if builder.Len() == 0 {
		return "step"
	}
	return builder.String()
}

func writeMarker(path, runID string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	payload, err := json.Marshal(map[string]any{
		"run_id":     runID,
		"pid":        os.Getpid(),
		"started_at": time.Now().UTC().Format(time.RFC3339),
	})
	if err != nil {
		return err
	}
	temp := path + ".tmp"
	if err := os.WriteFile(temp, payload, 0o644); err != nil {
		return err
	}
	_ = os.Remove(path)
	return os.Rename(temp, path)
}
