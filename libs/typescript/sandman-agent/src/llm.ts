import OpenAI from "openai";
import { traceHeaders, withSpan } from "./telemetry.ts";

export const LOCAL_ONLY = "local-only";

export class PolicyError extends Error {}
export class BudgetExceededError extends Error {}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}
export interface Usage {
  inputTokens: number;
  outputTokens: number;
}
export interface ChatRequest {
  model: string;
  messages: ChatMessage[];
  responseSchema?: Record<string, unknown>;
  temperature?: number;
  seed?: number;
  maxTokens?: number;
  user?: string;
}
export interface ChatResponse {
  message: ChatMessage;
  finishReason: string;
  usage: Usage;
  resolvedModel: string;
}

// 402 = gateway budget exhausted (blocks the workflow); 429 is already retried by the SDK
function gatewayError(e: unknown): unknown {
  return (e as { status?: number } | null)?.status === 402 ? new BudgetExceededError((e as Error).message) : e;
}

export class OpenAiLlmClient {
  private readonly sdk: Pick<OpenAI, "chat" | "embeddings">;

  constructor(
    baseUrl: string,
    apiKey: string,
    private readonly routes: Record<string, string>,
    private readonly localOnly = false,
    sdk?: Pick<OpenAI, "chat" | "embeddings">,
  ) {
    this.sdk = sdk ?? new OpenAI({ baseURL: baseUrl, apiKey, maxRetries: 2 });
  }

  private model(route: string): string {
    if (this.localOnly && !route.endsWith("-local")) {
      throw new PolicyError(`route '${route}' is not allowed for local-only projects`);
    }
    const model = this.routes[route];
    if (model === undefined) throw new PolicyError(`unknown route '${route}'`);
    return model;
  }

  async chat(req: ChatRequest): Promise<ChatResponse> {
    const model = this.model(req.model);
    return withSpan("sandman.llm.chat", { "gen_ai.request.model": model }, async () => {
      try {
        const resp = await this.sdk.chat.completions.create(
          {
            model,
            messages: req.messages,
            temperature: req.temperature ?? 0,
            ...(req.responseSchema && {
              response_format: {
                type: "json_schema" as const,
                json_schema: { name: "response", schema: req.responseSchema, strict: true },
              },
            }),
            ...(req.seed !== undefined && { seed: req.seed }),
            ...(req.maxTokens !== undefined && { max_tokens: req.maxTokens }),
            ...(req.user !== undefined && { user: req.user }),
          },
          { headers: traceHeaders() },
        );
        const choice = resp.choices[0]!;
        return {
          message: { role: "assistant", content: choice.message.content ?? "" },
          finishReason: choice.finish_reason,
          usage: { inputTokens: resp.usage?.prompt_tokens ?? 0, outputTokens: resp.usage?.completion_tokens ?? 0 },
          resolvedModel: resp.model,
        };
      } catch (e) {
        throw gatewayError(e);
      }
    });
  }

  async embed(route: string, texts: readonly string[]): Promise<number[][]> {
    const model = this.model(route);
    try {
      const resp = await this.sdk.embeddings.create({ model, input: [...texts] }, { headers: traceHeaders() });
      return resp.data.map((d) => d.embedding);
    } catch (e) {
      throw gatewayError(e);
    }
  }
}
