import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import neo4j from "neo4j-driver";
import { describe, expect, it } from "vitest";
import { ForbiddenError, TokenVerifier, actorFromMetadata, authenticatorFor } from "../src/auth.ts";
import { ConfigError, llmMode, requireSettings, settingsFromEnv } from "../src/config.ts";
import { Contracts } from "../src/contracts.ts";
import { DEFAULT_CONTRACTS_DIR } from "../src/config.ts";
import { EventPublisher, PublishError, handleMessage, buildEvent, CONSUMER_BACKOFF } from "../src/events.ts";
import { sha256Hex } from "../src/ids.ts";
import {
  KgClient, MemoryLimitError, RunRecorder, type KgDriver, type KgSession, type KgTx,
} from "../src/kg.ts";
import { BudgetExceededError, OpenAiLlmClient, PolicyError } from "../src/llm.ts";
import { StatusTracker } from "../src/status.ts";

const contracts = new Contracts(DEFAULT_CONTRACTS_DIR);
const validData = {
  projectId: "mediastore",
  sourceVersionIds: ["ver_0123456789abcdef0123456789abcdef"],
  targetVersionIds: ["ver_fedcba9876543210fedcba9876543210"],
  runs: [{ approach: "lissa", approachVersion: "1.0.0", runId: "run_0123456789abcdef0123456789abcdef", status: "completed", linkCount: 3 }],
};
const tmp = (name: string, content: string): string => {
  const path = join(mkdtempSync(join(tmpdir(), "sandman-")), name);
  writeFileSync(path, content);
  return path;
};

class FakeDriver implements KgDriver {
  mode: string[] = [];
  statements: { cypher: string; params?: Record<string, unknown> }[] = [];
  attempts = 0;
  fail: ((cypher: string) => Error | undefined) | undefined;

  session(): KgSession {
    const tx: KgTx = {
      run: async (cypher, params) => {
        this.attempts++;
        this.statements.push({ cypher, params });
        const err = this.fail?.(cypher);
        if (err) throw err;
        return { records: [{ toObject: () => ({ n: 1 }) }] };
      },
    };
    // like the real driver: transient errors are retried up to 3 times
    const withRetry = async <T>(mode: string, work: (t: KgTx) => Promise<T>): Promise<T> => {
      this.mode.push(mode);
      for (let i = 1; ; i++) {
        try {
          return await work(tx);
        } catch (e) {
          if (i >= 3 || !String((e as { code?: string }).code).startsWith("Neo.TransientError")) throw e;
        }
      }
    };
    return {
      executeRead: (w) => withRetry("read", w),
      executeWrite: (w) => withRetry("write", w),
      run: (cypher, params) => {
        this.mode.push("auto");
        return tx.run(cypher, params);
      },
      close: async () => {},
    };
  }
  close = async (): Promise<void> => {};
}

const coded = (code: string, message: string): Error => Object.assign(new Error(message), { code });

describe("kg", () => {
  it("read uses a read transaction and returns rows", async () => {
    const d = new FakeDriver();
    expect(await new KgClient("", undefined, undefined, d).read("MATCH (n) RETURN n")).toEqual([{ n: 1 }]);
    expect(d.mode).toEqual(["read"]);
  });

  it("does not retry the memory-limit error", async () => {
    const d = new FakeDriver();
    d.fail = () => coded("Neo.TransientError.General.MemoryPoolOutOfMemoryError", "limit");
    await expect(new KgClient("", undefined, undefined, d).read("MATCH (n) RETURN n")).rejects.toBeInstanceOf(MemoryLimitError);
    expect(d.attempts).toBe(1);
  });

  it("retries other transient errors (fake sanity check)", async () => {
    const d = new FakeDriver();
    d.fail = () => coded("Neo.TransientError.Transaction.DeadlockDetected", "deadlock");
    await expect(new KgClient("", undefined, undefined, d).write("MATCH (n) RETURN n")).rejects.toThrow("deadlock");
    expect(d.attempts).toBe(3);
  });

  it("initSchema skips comments and ignores 'already exists'", async () => {
    const d = new FakeDriver();
    d.fail = (c) => (c.includes("idx_a") ? new Error("Index already exists") : undefined);
    const file = tmp("init.cypher", "// comment\nCREATE INDEX idx_a FOR (n:A) ON (n.id);\nCREATE INDEX idx_b FOR (n:B) ON (n.id);\n");
    await new KgClient("", undefined, undefined, d).initSchema(file);
    expect(d.statements.map((s) => s.cypher.slice(0, 20))).toEqual(["CREATE INDEX idx_a F", "CREATE INDEX idx_b F"]);
    d.fail = () => new Error("syntax error");
    await expect(new KgClient("", undefined, undefined, d).initSchema(file)).rejects.toThrow("syntax error");
  });

  it("RunRecorder passes token counts as integers", async () => {
    const d = new FakeDriver();
    const recorder = new RunRecorder(new KgClient("", undefined, undefined, d), "tlr-agent", "1");
    await recorder.complete("run_x", { inputTokens: 7, outputTokens: 3 });
    const params = d.statements[0]!.params!;
    expect(neo4j.isInt(params.inp) && neo4j.isInt(params.out)).toBe(true);
    const id = await recorder.start({
      projectId: "p", skill: "s", tool: "t", toolVersion: "1", inputKey: "k", replayMode: "live",
    });
    expect(id).toMatch(/^run_[0-9a-f]{32}$/);
    const props = d.statements[1]!.params!.props as Record<string, unknown>;
    expect(props).toMatchObject({ id, status: "running", replayMode: "live" });
    expect("model" in props).toBe(false);
  });
});

describe("auth", () => {
  const verifier = new TokenVerifier({ orchestrator: sha256Hex("secret"), "plugin:x": sha256Hex("p") });
  it("maps bearer tokens to callers", () => {
    expect(verifier.callerFromHeader("Bearer secret")).toBe("orchestrator");
    expect(verifier.callerFromHeader("bearer secret")).toBe("orchestrator");
    expect(verifier.callerFromHeader("Bearer wrong")).toBeUndefined();
    expect(verifier.callerFromHeader("secret")).toBeUndefined();
    expect(verifier.callerFromHeader(undefined)).toBeUndefined();
  });
  it("rejects plugin callers on agents and accepts the inbound token on plugins", () => {
    const agent = authenticatorFor({ serviceVerify: { orchestrator: sha256Hex("secret"), "plugin:x": sha256Hex("p") } });
    expect(agent("Bearer secret")).toBe("orchestrator");
    expect(agent("Bearer p")).toBeUndefined();
    const plugin = authenticatorFor({ serviceVerify: {}, pluginInboundToken: "in" });
    expect(plugin("Bearer in")).toBe("owner");
    expect(plugin("Bearer out")).toBeUndefined();
  });
  it("accepts sandman.actor only from the orchestrator", () => {
    expect(actorFromMetadata("orchestrator", { "sandman.actor": "alice" })).toBe("alice");
    expect(actorFromMetadata("x", {})).toBeUndefined();
    expect(() => actorFromMetadata("query-service", { "sandman.actor": "alice" })).toThrow(ForbiddenError);
  });
});

describe("status", () => {
  it("reports working while a task runs", async () => {
    const t = new StatusTracker("svc", "1");
    expect(t.snapshot().state).toBe("idle");
    let during: unknown;
    await t.track(async () => {
      during = t.snapshot();
    });
    expect(during).toMatchObject({ state: "working", activeTasks: 1 });
    expect(t.snapshot()).toMatchObject({ state: "idle", activeTasks: 0 });
    expect(t.snapshot().lastActivityAt).toBeTypeOf("string");
  });
});

describe("config", () => {
  it("reads settings and *_FILE secrets", () => {
    const token = tmp("token", "s3cret\n");
    const s = settingsFromEnv({ SANDMAN_SERVICE_NAME: "svc", SANDMAN_SERVICE_TOKEN_FILE: token, SANDMAN_KG_URI: "bolt://kg" });
    expect(s).toMatchObject({ serviceName: "svc", serviceToken: "s3cret", kgUri: "bolt://kg", serviceVerify: {} });
  });
  it("fails on missing name, unreadable file, bad mode and missing required settings", () => {
    expect(() => settingsFromEnv({})).toThrow(ConfigError);
    expect(() => settingsFromEnv({ SANDMAN_SERVICE_NAME: "a", SANDMAN_KG_PASSWORD_FILE: "/nonexistent" })).toThrow(ConfigError);
    expect(() => llmMode({ SANDMAN_LLM_MODE: "x" })).toThrow(ConfigError);
    expect(llmMode({})).toBe("live");
    expect(() => requireSettings(settingsFromEnv({ SANDMAN_SERVICE_NAME: "a" }), "kgUri", "natsUrl")).toThrow(
      "a: missing configuration SANDMAN_KG_URI, SANDMAN_NATS_URL",
    );
  });
});

describe("events", () => {
  it("publishes with msgID, retries, then raises PublishError", async () => {
    const calls: { subject: string; options: unknown }[] = [];
    let failures = 2;
    const js = {
      publish: async (subject: string, _p: unknown, options: unknown) => {
        calls.push({ subject, options });
        if (failures-- > 0) throw new Error("down");
        return {} as never;
      },
    };
    const event = await new EventPublisher(js, contracts, "tlr-agent", [0, 0, 0]).publish(
      "sandman.tracelinks.updated", "mediastore", validData, { causationId: "task-1" },
    );
    expect(calls).toHaveLength(3);
    expect(calls[0]!.subject).toBe("sandman.tracelinks.updated");
    expect(calls[2]!.options).toEqual({ msgID: event.id });
    const down = { publish: async () => Promise.reject(new Error("down")) };
    await expect(
      new EventPublisher(down, contracts, "tlr-agent", [0, 0, 0]).publish(
        "sandman.tracelinks.updated", "mediastore", validData, { causationId: "task-1" },
      ),
    ).rejects.toBeInstanceOf(PublishError);
  });

  it("rejects an invalid envelope before publishing", () => {
    expect(() => buildEvent(contracts, "tlr-agent", "sandman.tracelinks.updated", "mediastore", { projectId: "mediastore" })).toThrow();
  });

  it("acks after success and naks with the backoff delay on failure", async () => {
    const make = (deliveryCount: number) => {
      const log: string[] = [];
      const event = {
        specversion: "1.0", id: "0b6f4a0e-8f6e-4c6b-9d7a-1f2e3d4c5b6a", source: "sandman/x", type: "sandman.x",
        subject: "p", time: "2026-10-06T12:00:00Z", datacontenttype: "application/json", data: {},
      };
      return {
        log,
        msg: {
          data: new TextEncoder().encode(JSON.stringify(event)), info: { deliveryCount },
          ack: () => void log.push("ack"), nak: (ms?: number) => void log.push(`nak ${ms}`),
        },
      };
    };
    const ok = make(1);
    await handleMessage(ok.msg, async () => {});
    expect(ok.log).toEqual(["ack"]);
    const bad = make(2);
    await handleMessage(bad.msg, async () => Promise.reject(new Error("boom")));
    expect(bad.log).toEqual([`nak ${CONSUMER_BACKOFF[1]! * 1000}`]);
  });
});

describe("llm", () => {
  const sdk = (create: () => Promise<unknown>) => ({ chat: { completions: { create } }, embeddings: { create } }) as never;
  it("maps HTTP 402 to BudgetExceededError", async () => {
    const client = new OpenAiLlmClient("", "k", { "chat-default": "m" }, false, sdk(async () => {
      throw Object.assign(new Error("budget"), { status: 402 });
    }));
    await expect(client.chat({ model: "chat-default", messages: [] })).rejects.toBeInstanceOf(BudgetExceededError);
  });
  it("enforces the local-only policy and unknown routes", async () => {
    const client = new OpenAiLlmClient("", "k", { "chat-default": "m" }, true, sdk(async () => ({})));
    await expect(client.chat({ model: "chat-default", messages: [] })).rejects.toBeInstanceOf(PolicyError);
    await expect(client.embed("nope-local", ["x"])).rejects.toBeInstanceOf(PolicyError);
  });
  it("returns content, usage and the resolved model", async () => {
    const client = new OpenAiLlmClient("", "k", { "chat-default": "m" }, false, sdk(async () => ({
      model: "resolved", choices: [{ message: { content: "hi" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 4, completion_tokens: 2 },
    })));
    expect(await client.chat({ model: "chat-default", messages: [] })).toEqual({
      message: { role: "assistant", content: "hi" }, finishReason: "stop",
      usage: { inputTokens: 4, outputTokens: 2 }, resolvedModel: "resolved",
    });
  });
});
