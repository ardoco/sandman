package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func env(m map[string]string) Env { return func(k string) string { return m[k] } }

func TestFromEnvReadsFileSecrets(t *testing.T) {
	token := filepath.Join(t.TempDir(), "token")
	_ = os.WriteFile(token, []byte("s3cret\n"), 0o600)
	s, err := FromEnv(env(map[string]string{"SANDMAN_SERVICE_NAME": "svc", "SANDMAN_SERVICE_TOKEN_FILE": token, "SANDMAN_KG_URI": "bolt://kg"}))
	if err != nil || s.ServiceName != "svc" || s.ServiceToken != "s3cret" || s.KGURI != "bolt://kg" || len(s.ServiceVerify) != 0 {
		t.Fatalf("%+v %v", s, err)
	}
}

func TestFromEnvFailures(t *testing.T) {
	if _, err := FromEnv(env(nil)); err == nil {
		t.Fatal("missing name accepted")
	}
	if _, err := FromEnv(env(map[string]string{"SANDMAN_SERVICE_NAME": "a", "SANDMAN_KG_PASSWORD_FILE": "/nonexistent"})); err == nil {
		t.Fatal("unreadable file accepted")
	}
	if _, err := LLMMode(env(map[string]string{"SANDMAN_LLM_MODE": "x"})); err == nil {
		t.Fatal("bad mode accepted")
	}
	if m, _ := LLMMode(env(nil)); m != "live" {
		t.Fatal(m)
	}
	s, _ := FromEnv(env(map[string]string{"SANDMAN_SERVICE_NAME": "a"}))
	err := s.Require("SANDMAN_KG_URI", "SANDMAN_NATS_URL")
	if err == nil || !strings.Contains(err.Error(), "a: missing configuration SANDMAN_KG_URI, SANDMAN_NATS_URL") {
		t.Fatal(err)
	}
}
