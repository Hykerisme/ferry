package main

import (
	"encoding/json"
	"io"
	"sync"
)

type LockSpec struct {
	Path string `json:"path"`
}

type MarkerSpec struct {
	Path string `json:"path"`
}

type Step struct {
	ID         string            `json:"id"`
	Where      string            `json:"where"`
	Kind       string            `json:"kind"`
	Argv       []string          `json:"argv"`
	Cwd        string            `json:"cwd"`
	Env        map[string]string `json:"env"`
	TimeoutMS  int               `json:"timeout_ms"`
	Result     bool              `json:"result"`
	ScriptB64  string            `json:"script_b64"`
	TargetHost string            `json:"target_host"`
}

type ExecRequest struct {
	V          int         `json:"v"`
	RunID      string      `json:"run_id"`
	Workdir    string      `json:"workdir"`
	TargetHost string      `json:"target_host"`
	TargetKey  string      `json:"target_key"`
	Lock       *LockSpec   `json:"lock"`
	Marker     *MarkerSpec `json:"marker"`
	CleanPaths []string    `json:"clean_paths"`
	Steps      []Step      `json:"steps"`
}

type Result struct {
	State     string `json:"state"`
	Text      string `json:"text"`
	Truncated bool   `json:"truncated"`
}

type Event struct {
	Event      string   `json:"event"`
	ID         string   `json:"id,omitempty"`
	Stream     string   `json:"stream,omitempty"`
	Chunk      string   `json:"chunk,omitempty"`
	Code       int      `json:"code,omitempty"`
	DurationMS int      `json:"duration_ms,omitempty"`
	Tail       string   `json:"tail,omitempty"`
	Result     *Result  `json:"result,omitempty"`
	PID        int      `json:"pid,omitempty"`
	PGID       int      `json:"pgid,omitempty"`
	Message    string   `json:"message,omitempty"`
	Cleaned    []string `json:"cleaned,omitempty"`
}

type emitter struct {
	mu     sync.Mutex
	enc    *json.Encoder
	failed bool
}

func newEmitter(w io.Writer) *emitter {
	return &emitter{enc: json.NewEncoder(w)}
}

func (e *emitter) emit(event Event) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.failed {
		return false
	}
	if err := e.enc.Encode(event); err != nil {
		e.failed = true
		return false
	}
	return true
}

func (e *emitter) hasFailed() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.failed
}
