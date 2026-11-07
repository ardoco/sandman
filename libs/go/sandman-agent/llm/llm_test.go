package llm

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

var routes = map[string]string{"chat-default": "m"}

func gateway(t *testing.T, status int, body string) *httptest.Server {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(s.Close)
	return s
}

func TestHTTP402IsBudgetExceeded(t *testing.T) {
	s := gateway(t, 402, `{"error":{"message":"budget","type":"budget"}}`)
	_, err := New(s.URL, "k", routes, false).Chat(context.Background(), ChatRequest{Model: "chat-default"})
	var be *BudgetExceededError
	if !errors.As(err, &be) {
		t.Fatalf("got %v", err)
	}
	_, err = New(s.URL, "k", map[string]string{"e-local": "m"}, false).Embed(context.Background(), "e-local", []string{"x"})
	if !errors.As(err, &be) {
		t.Fatalf("embed: got %v", err)
	}
}

func TestPolicyAndUnknownRoutes(t *testing.T) {
	c := New("http://127.0.0.1:1", "k", routes, true)
	var pe *PolicyError
	if _, err := c.Chat(context.Background(), ChatRequest{Model: "chat-default"}); !errors.As(err, &pe) {
		t.Fatal(err)
	}
	if _, err := c.Embed(context.Background(), "nope-local", []string{"x"}); !errors.As(err, &pe) {
		t.Fatal(err)
	}
}

func TestChatReturnsContentUsageAndResolvedModel(t *testing.T) {
	s := gateway(t, 200, `{"id":"1","object":"chat.completion","created":1,"model":"resolved","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"hi"}}],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}`)
	got, err := New(s.URL, "k", routes, false).Chat(context.Background(), ChatRequest{Model: "chat-default", Messages: []Message{{"user", "x"}}})
	if err != nil || got.Message.Content != "hi" || got.FinishReason != "stop" || got.Usage != (Usage{4, 2}) || got.ResolvedModel != "resolved" {
		t.Fatalf("%+v %v", got, err)
	}
}
