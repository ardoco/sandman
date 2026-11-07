// Package llm is the client of the SANDMAN LLM gateway on openai-go.
package llm

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/telemetry"
	"github.com/openai/openai-go/v3"
	"github.com/openai/openai-go/v3/option"
	"github.com/openai/openai-go/v3/shared"
)

const LocalOnly = "local-only"

type PolicyError struct{ Msg string }

func (e *PolicyError) Error() string { return e.Msg }

// BudgetExceededError is the gateway answer HTTP 402: the budget is exhausted and the workflow blocks.
type BudgetExceededError struct{ Msg string }

func (e *BudgetExceededError) Error() string { return e.Msg }

type Message struct{ Role, Content string }

type Usage struct{ InputTokens, OutputTokens int64 }

type ChatRequest struct {
	Model          string // route name
	Messages       []Message
	ResponseSchema map[string]any
	Temperature    *float64
	Seed           *int64
	MaxTokens      *int64
	User           string
}

type ChatResponse struct {
	Message       Message
	FinishReason  string
	Usage         Usage
	ResolvedModel string
}

// 402 = gateway budget exhausted; 429 is already retried by the SDK.
func gatewayError(err error) error {
	var apiErr *openai.Error
	if errors.As(err, &apiErr) && apiErr.StatusCode == http.StatusPaymentRequired {
		return &BudgetExceededError{err.Error()}
	}
	return err
}

type Client struct {
	sdk       openai.Client
	routes    map[string]string
	localOnly bool
}

func New(baseURL, apiKey string, routes map[string]string, localOnly bool, opts ...option.RequestOption) *Client {
	base := []option.RequestOption{option.WithBaseURL(baseURL), option.WithAPIKey(apiKey), option.WithMaxRetries(2)}
	return &Client{sdk: openai.NewClient(append(base, opts...)...), routes: routes, localOnly: localOnly}
}

func (c *Client) model(route string) (string, error) {
	if c.localOnly && !strings.HasSuffix(route, "-local") {
		return "", &PolicyError{fmt.Sprintf("route '%s' is not allowed for local-only projects", route)}
	}
	m, ok := c.routes[route]
	if !ok {
		return "", &PolicyError{fmt.Sprintf("unknown route '%s'", route)}
	}
	return m, nil
}

func traceOpts(ctx context.Context) []option.RequestOption {
	if tp := telemetry.Traceparent(ctx); tp != "" {
		return []option.RequestOption{option.WithHeader("traceparent", tp)}
	}
	return nil
}

func (c *Client) Chat(ctx context.Context, req ChatRequest) (*ChatResponse, error) {
	model, err := c.model(req.Model)
	if err != nil {
		return nil, err
	}
	var out *ChatResponse
	err = telemetry.WithSpan(ctx, "sandman.llm.chat", map[string]string{"gen_ai.request.model": model}, "", func(ctx context.Context) error {
		params := openai.ChatCompletionNewParams{Model: model, Temperature: openai.Float(0)}
		for _, m := range req.Messages {
			switch m.Role {
			case "system":
				params.Messages = append(params.Messages, openai.SystemMessage(m.Content))
			case "assistant":
				params.Messages = append(params.Messages, openai.AssistantMessage(m.Content))
			default:
				params.Messages = append(params.Messages, openai.UserMessage(m.Content))
			}
		}
		if req.Temperature != nil {
			params.Temperature = openai.Float(*req.Temperature)
		}
		if req.Seed != nil {
			params.Seed = openai.Int(*req.Seed)
		}
		if req.MaxTokens != nil {
			params.MaxTokens = openai.Int(*req.MaxTokens)
		}
		if req.User != "" {
			params.User = openai.String(req.User)
		}
		if req.ResponseSchema != nil {
			params.ResponseFormat = openai.ChatCompletionNewParamsResponseFormatUnion{
				OfJSONSchema: &shared.ResponseFormatJSONSchemaParam{JSONSchema: shared.ResponseFormatJSONSchemaJSONSchemaParam{
					Name: "response", Schema: req.ResponseSchema, Strict: openai.Bool(true)}},
			}
		}
		resp, err := c.sdk.Chat.Completions.New(ctx, params, traceOpts(ctx)...)
		if err != nil {
			return gatewayError(err)
		}
		if len(resp.Choices) == 0 {
			return errors.New("gateway returned no choices")
		}
		choice := resp.Choices[0]
		out = &ChatResponse{
			Message: Message{Role: "assistant", Content: choice.Message.Content}, FinishReason: choice.FinishReason,
			Usage: Usage{resp.Usage.PromptTokens, resp.Usage.CompletionTokens}, ResolvedModel: resp.Model,
		}
		return nil
	})
	return out, err
}

func (c *Client) Embed(ctx context.Context, route string, texts []string) ([][]float64, error) {
	model, err := c.model(route)
	if err != nil {
		return nil, err
	}
	resp, err := c.sdk.Embeddings.New(ctx, openai.EmbeddingNewParams{
		Model: model, Input: openai.EmbeddingNewParamsInputUnion{OfArrayOfStrings: texts},
	}, traceOpts(ctx)...)
	if err != nil {
		return nil, gatewayError(err)
	}
	out := make([][]float64, len(resp.Data))
	for i, d := range resp.Data {
		out[i] = d.Embedding
	}
	return out, nil
}
