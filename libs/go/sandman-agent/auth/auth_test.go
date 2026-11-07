package auth

import (
	"errors"
	"testing"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/ids"
)

func TestBearerTokensMapToCallers(t *testing.T) {
	v := NewTokenVerifier(map[string]string{"orchestrator": ids.Sha256Hex("secret"), "plugin:x": ids.Sha256Hex("p")})
	for header, want := range map[string]string{"Bearer secret": "orchestrator", "bearer secret": "orchestrator", "Bearer wrong": "", "secret": "", "": ""} {
		if got, _ := v.CallerFromHeader(header); got != want {
			t.Errorf("%q: got %q want %q", header, got, want)
		}
	}
}

func TestAuthenticators(t *testing.T) {
	verify := map[string]string{"orchestrator": ids.Sha256Hex("secret"), "plugin:x": ids.Sha256Hex("p")}
	agent := AuthenticatorFor("", verify)
	if c, ok := agent("Bearer secret"); !ok || c != "orchestrator" {
		t.Fatal(c)
	}
	if _, ok := agent("Bearer p"); ok {
		t.Fatal("plugin accepted on agent")
	}
	plugin := AuthenticatorFor("in", nil)
	if c, ok := plugin("Bearer in"); !ok || c != "owner" {
		t.Fatal(c)
	}
	if _, ok := plugin("Bearer out"); ok {
		t.Fatal("wrong token accepted")
	}
}

func TestActorOnlyFromOrchestrator(t *testing.T) {
	if a, err := ActorFromMetadata("orchestrator", map[string]any{ActorKey: "alice"}); a != "alice" || err != nil {
		t.Fatal(a, err)
	}
	if a, err := ActorFromMetadata("x", nil); a != "" || err != nil {
		t.Fatal(a, err)
	}
	if _, err := ActorFromMetadata("query-service", map[string]any{ActorKey: "alice"}); !errors.Is(err, ErrForbidden) {
		t.Fatal(err)
	}
}
