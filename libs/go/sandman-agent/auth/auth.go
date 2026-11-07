// Package auth maps bearer tokens to callers.
package auth

import (
	"crypto/subtle"
	"errors"
	"strings"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/ids"
)

const (
	QueryService = "query-service"
	Orchestrator = "orchestrator"
	PluginPrefix = "plugin:"
	ActorKey     = "sandman.actor"
)

var ErrForbidden = errors.New("sandman.actor is accepted only from the orchestrator")

// TokenVerifier compares sha256 hex digests of tokens in constant time.
type TokenVerifier struct{ hashes map[string]string }

func NewTokenVerifier(hashes map[string]string) *TokenVerifier { return &TokenVerifier{hashes} }

func (v *TokenVerifier) Caller(token string) (string, bool) {
	if token == "" {
		return "", false
	}
	digest := []byte(ids.Sha256Hex(token))
	found := ""
	for name, expected := range v.hashes {
		if subtle.ConstantTimeCompare(digest, []byte(expected)) == 1 {
			found = name
		}
	}
	return found, found != ""
}

func (v *TokenVerifier) CallerFromHeader(authorization string) (string, bool) {
	token, ok := BearerToken(authorization)
	if !ok {
		return "", false
	}
	return v.Caller(token)
}

func BearerToken(authorization string) (string, bool) {
	if len(authorization) < 7 || !strings.EqualFold(authorization[:7], "bearer ") {
		return "", false
	}
	token := strings.TrimSpace(authorization[7:])
	return token, token != ""
}

// ActorFromMetadata returns the actor claim. Only the orchestrator may set it.
func ActorFromMetadata(caller string, metadata map[string]any) (string, error) {
	actor, ok := metadata[ActorKey]
	if !ok || actor == nil {
		return "", nil
	}
	if caller != Orchestrator {
		return "", ErrForbidden
	}
	s, _ := actor.(string)
	return s, nil
}

// Authenticator returns the caller for an Authorization header value.
type Authenticator func(authorization string) (string, bool)

func AuthenticatorFor(pluginInboundToken string, serviceVerify map[string]string) Authenticator {
	if pluginInboundToken != "" {
		want := []byte(ids.Sha256Hex(pluginInboundToken))
		return func(authorization string) (string, bool) {
			token, ok := BearerToken(authorization)
			if !ok || subtle.ConstantTimeCompare([]byte(ids.Sha256Hex(token)), want) != 1 {
				return "", false
			}
			return "owner", true
		}
	}
	verifier := NewTokenVerifier(serviceVerify)
	return func(authorization string) (string, bool) {
		caller, ok := verifier.CallerFromHeader(authorization)
		if !ok || strings.HasPrefix(caller, PluginPrefix) {
			return "", false
		}
		return caller, true
	}
}
