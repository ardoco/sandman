// Package status tracks running tasks and serves GET /v1/status.
package status

import (
	"encoding/json"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/auth"
)

type Tracker struct {
	Service, Version string
	mu               sync.Mutex
	active           int
	last             string
}

func NewTracker(service string) *Tracker {
	v := os.Getenv("SANDMAN_VERSION")
	if v == "" {
		v = "dev"
	}
	return &Tracker{Service: service, Version: v}
}

func (t *Tracker) touch() { t.last = time.Now().UTC().Format("2006-01-02T15:04:05Z") }

func (t *Tracker) Track(fn func() error) error {
	t.mu.Lock()
	t.active++
	t.touch()
	t.mu.Unlock()
	defer func() {
		t.mu.Lock()
		t.active--
		t.touch()
		t.mu.Unlock()
	}()
	return fn()
}

func (t *Tracker) Snapshot(details map[string]any) map[string]any {
	t.mu.Lock()
	defer t.mu.Unlock()
	state := "idle"
	if t.active > 0 {
		state = "working"
	}
	out := map[string]any{"service": t.Service, "version": t.Version, "state": state, "activeTasks": t.active}
	if t.last != "" {
		out["lastActivityAt"] = t.last
	}
	if len(details) > 0 {
		out["details"] = details
	}
	return out
}

func Handler(t *Tracker, authenticate auth.Authenticator, details func() map[string]any) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if _, ok := authenticate(r.Header.Get("Authorization")); !ok {
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "missing or invalid token"})
			return
		}
		var d map[string]any
		if details != nil {
			d = details()
		}
		_ = json.NewEncoder(w).Encode(t.Snapshot(d))
	}
}
