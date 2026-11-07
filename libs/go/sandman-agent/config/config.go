// Package config reads Settings from SANDMAN_* variables and *_FILE secrets.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

type Error struct{ Msg string }

func (e *Error) Error() string { return e.Msg }

func errf(format string, a ...any) error { return &Error{fmt.Sprintf(format, a...)} }

var LLMModes = []string{"live", "record", "replay"}

type Env func(string) string

func OSEnv(k string) string { return os.Getenv(k) }

// DefaultContractsDir resolves ../../../../contracts relative to this source file's package root.
func DefaultContractsDir() string {
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "..", "..", "..", "..", "contracts")
}

func LLMMode(env Env) (string, error) {
	mode := env("SANDMAN_LLM_MODE")
	if mode == "" {
		mode = "live"
	}
	for _, m := range LLMModes {
		if m == mode {
			return mode, nil
		}
	}
	return "", errf("invalid SANDMAN_LLM_MODE '%s', expected one of %s", mode, strings.Join(LLMModes, ", "))
}

type Settings struct {
	ServiceName        string
	ContractsDir       string
	LLMBaseURL         string
	LLMKey             string
	LLMLocalKey        string
	KGURI              string
	KGUser             string
	KGPassword         string
	NATSURL            string
	ServiceToken       string
	ServiceVerify      map[string]string
	PluginInboundToken string
}

func readFile(name, path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", errf("cannot read %s=%s: %v", name, path, err)
	}
	return string(b), nil
}

func FromEnv(env Env) (*Settings, error) {
	var firstErr error
	secret := func(name string) string {
		file := env(name + "_FILE")
		if file == "" {
			return env(name)
		}
		v, err := readFile(name+"_FILE", file)
		if err != nil && firstErr == nil {
			firstErr = err
		}
		return strings.TrimSpace(v)
	}
	name := env("SANDMAN_SERVICE_NAME")
	if name == "" {
		return nil, errf("SANDMAN_SERVICE_NAME is required")
	}
	contracts := env("SANDMAN_CONTRACTS_DIR")
	if contracts == "" {
		contracts = DefaultContractsDir()
	}
	s := &Settings{
		ServiceName: name, ContractsDir: contracts, ServiceVerify: map[string]string{},
		LLMBaseURL: env("SANDMAN_LLM_BASE_URL"), LLMKey: secret("SANDMAN_LLM_KEY"), LLMLocalKey: secret("SANDMAN_LLM_LOCAL_KEY"),
		KGURI: env("SANDMAN_KG_URI"), KGUser: env("SANDMAN_KG_USER"), KGPassword: secret("SANDMAN_KG_PASSWORD"),
		NATSURL: env("SANDMAN_NATS_URL"), ServiceToken: secret("SANDMAN_SERVICE_TOKEN"),
		PluginInboundToken: secret("SANDMAN_PLUGIN_INBOUND_TOKEN"),
	}
	if verify := env("SANDMAN_SERVICE_VERIFY_FILE"); verify != "" {
		raw, err := readFile("SANDMAN_SERVICE_VERIFY_FILE", verify)
		if err == nil {
			if err = json.Unmarshal([]byte(raw), &s.ServiceVerify); err != nil {
				err = errf("cannot read SANDMAN_SERVICE_VERIFY_FILE=%s: %v", verify, err)
			}
		}
		if err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return s, firstErr
}

// Require fails when a named setting is empty. Names are the SANDMAN_* variable names.
func (s *Settings) Require(names ...string) error {
	values := map[string]bool{
		"SANDMAN_LLM_BASE_URL": s.LLMBaseURL != "", "SANDMAN_LLM_KEY": s.LLMKey != "", "SANDMAN_LLM_LOCAL_KEY": s.LLMLocalKey != "",
		"SANDMAN_KG_URI": s.KGURI != "", "SANDMAN_KG_USER": s.KGUser != "", "SANDMAN_KG_PASSWORD": s.KGPassword != "",
		"SANDMAN_NATS_URL": s.NATSURL != "", "SANDMAN_SERVICE_TOKEN": s.ServiceToken != "",
		"SANDMAN_SERVICE_VERIFY": len(s.ServiceVerify) > 0, "SANDMAN_PLUGIN_INBOUND_TOKEN": s.PluginInboundToken != "",
	}
	var missing []string
	for _, n := range names {
		if !values[n] {
			missing = append(missing, n)
		}
	}
	if len(missing) > 0 {
		return errf("%s: missing configuration %s", s.ServiceName, strings.Join(missing, ", "))
	}
	return nil
}
