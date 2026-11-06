import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import {
  AgentCard,
  Role,
  TaskState,
  type Artifact,
  type Message,
  type Part,
} from "@a2a-js/sdk";
import {
  AgentEvent,
  DefaultRequestHandler,
  InMemoryTaskStore,
  type AgentExecutor,
  type ExecutionEventBus,
  type RequestContext,
} from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler } from "@a2a-js/sdk/server/express";
import express, { type NextFunction, type Request, type Response } from "express";
import { ForbiddenError, actorFromMetadata, authenticatorFor, type Authenticator } from "./auth.ts";
import { ContractError, Contracts } from "./contracts.ts";
import type { Settings } from "./config.ts";
import type { EventSink } from "./events.ts";
import { canonicalJson } from "./ids.ts";
import { BudgetExceededError } from "./llm.ts";
import { StatusTracker, statusHandler } from "./status.ts";
import { withSpan } from "./telemetry.ts";

export const SKILL_KEY = "sandman.skill";
export const WORKFLOW_KEY = "sandman.workflowId";
export const ACTOR_KEY = "sandman.actor";
export const ERROR_KEY = "sandman.error";
export const INVOCATION_EXTENSION = "https://sandman.invalid/a2a/extensions/skill-invocation/v1";
const MAX_SEEN = 1024;
const ACTIVITY_INTERVAL_MS = 2000;

export class SkillError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable: boolean,
  ) {
    super(message);
  }

  toJson(): { code: string; message: string; retryable: boolean } {
    return { code: this.code, message: this.message, retryable: this.retryable };
  }
}

export interface SkillResult {
  output: Record<string, unknown>;
  eventType?: string;
  eventData?: Record<string, unknown>;
}

export interface SkillContext {
  taskId: string;
  messageId: string;
  workflowId?: string;
  caller: string;
  actor?: string;
  settings: Settings;
  signal: AbortSignal;
  progress(text: string): void;
}

export interface Skill {
  id: string;
  run(ctx: SkillContext, input: Record<string, unknown>): Promise<SkillResult>;
}

interface Outcome {
  output?: Record<string, unknown>;
  error?: SkillError;
  reject: boolean;
}

const timestamp = (): string => new Date().toISOString();

function textPart(text: string): Part {
  return { content: { $case: "text", value: text }, metadata: undefined, filename: "", mediaType: "text/plain" };
}

function dataPart(value: unknown): Part {
  return { content: { $case: "data", value }, metadata: undefined, filename: "", mediaType: "application/json" };
}

class Executor implements AgentExecutor {
  private readonly running = new Map<string, { contextId: string; abort: AbortController }>();
  private readonly seen = new Map<string, { fingerprint: string; done: Promise<Outcome>; settled: boolean }>();

  constructor(
    private readonly skills: ReadonlyMap<string, Skill>,
    private readonly schemas: Readonly<Record<string, { inputSchema: string; outputSchema: string }>>,
    private readonly planned: ReadonlySet<string>,
    private readonly settings: Settings,
    private readonly contracts: Contracts,
    private readonly events: EventSink | undefined,
    private readonly status: StatusTracker,
  ) {}

  private setState(bus: ExecutionEventBus, ctx: RequestContext, state: TaskState, message?: Message): void {
    bus.publish(
      AgentEvent.statusUpdate({
        taskId: ctx.taskId,
        contextId: ctx.contextId,
        status: { state, message, timestamp: timestamp() },
        metadata: undefined,
      }),
    );
  }

  private agentMessage(ctx: RequestContext, parts: Part[], metadata?: Record<string, unknown>): Message {
    return {
      messageId: randomUUID(),
      contextId: ctx.contextId,
      taskId: ctx.taskId,
      role: Role.ROLE_AGENT,
      parts,
      metadata,
      extensions: [],
      referenceTaskIds: [],
    };
  }

  private end(bus: ExecutionEventBus, ctx: RequestContext, outcome: Outcome): void {
    if (outcome.error) {
      const msg = this.agentMessage(ctx, [textPart(outcome.error.message)], { [ERROR_KEY]: outcome.error.toJson() });
      this.setState(bus, ctx, outcome.reject ? TaskState.TASK_STATE_REJECTED : TaskState.TASK_STATE_FAILED, msg);
    } else {
      const artifact: Artifact = {
        artifactId: randomUUID(),
        name: "result",
        description: "",
        parts: [dataPart(outcome.output ?? {})],
        metadata: undefined,
        extensions: [],
      };
      bus.publish(
        AgentEvent.artifactUpdate({
          taskId: ctx.taskId,
          contextId: ctx.contextId,
          artifact,
          append: false,
          lastChunk: true,
          metadata: undefined,
        }),
      );
      this.setState(bus, ctx, TaskState.TASK_STATE_COMPLETED);
    }
    bus.finished();
  }

  async execute(ctx: RequestContext, bus: ExecutionEventBus): Promise<void> {
    bus.publish(
      AgentEvent.task({
        id: ctx.taskId,
        contextId: ctx.contextId,
        status: { state: TaskState.TASK_STATE_SUBMITTED, message: undefined, timestamp: timestamp() },
        artifacts: [],
        history: [],
        metadata: undefined,
      }),
    );
    const message = ctx.userMessage;
    const metadata = (message.metadata ?? {}) as Record<string, unknown>;
    const reject = (code: string, text: string): void =>
      this.end(bus, ctx, { error: new SkillError(code, text, false), reject: true });

    const skillId = metadata[SKILL_KEY];
    if (typeof skillId !== "string") return reject("INVALID_INPUT", `metadata ${SKILL_KEY} missing`);
    const skill = this.skills.get(skillId);
    if (skill === undefined && this.planned.has(skillId)) {
      return reject("NOT_IMPLEMENTED", `skill ${skillId} is planned, not implemented`);
    }
    const schema = this.schemas[skillId];
    if (skill === undefined || schema === undefined) return reject("UNKNOWN_SKILL", `unknown skill ${skillId}`);
    const dataParts = message.parts.filter((p) => p.content?.$case === "data");
    if (dataParts.length !== 1) return reject("INVALID_INPUT", "expected exactly one data part");
    const input = (dataParts[0]!.content as { value: Record<string, unknown> }).value;
    try {
      this.contracts.validate(schema.inputSchema, input);
    } catch (e) {
      if (e instanceof ContractError) return reject("INVALID_INPUT", e.message);
      throw e;
    }
    const caller = ctx.context.user?.userName ?? "";
    let actor: string | undefined;
    try {
      actor = actorFromMetadata(caller, metadata);
    } catch (e) {
      if (e instanceof ForbiddenError) return reject("FORBIDDEN", e.message);
      throw e;
    }

    const key = `${caller}\x1f${message.messageId}`;
    const fingerprint = canonicalJson([skillId, input]);
    const previous = this.seen.get(key);
    if (previous) {
      if (previous.fingerprint !== fingerprint) {
        return reject("INVALID_INPUT", "messageId reused with a different request");
      }
      this.setState(bus, ctx, TaskState.TASK_STATE_WORKING);
      this.end(bus, ctx, await previous.done);
      return;
    }

    const entry = { fingerprint, settled: false, done: undefined as unknown as Promise<Outcome> };
    this.seen.set(key, entry);
    for (const [k, v] of this.seen) {
      if (this.seen.size <= MAX_SEEN) break;
      if (v.settled) this.seen.delete(k);
    }
    const abort = new AbortController();
    this.running.set(ctx.taskId, { contextId: ctx.contextId, abort });
    let lastProgress = Number.NEGATIVE_INFINITY;
    const skillCtx: SkillContext = {
      taskId: ctx.taskId,
      messageId: message.messageId,
      workflowId: typeof metadata[WORKFLOW_KEY] === "string" ? (metadata[WORKFLOW_KEY] as string) : undefined,
      caller,
      actor,
      settings: this.settings,
      signal: abort.signal,
      progress: (text) => {
        const t = Date.now();
        if (t - lastProgress < ACTIVITY_INTERVAL_MS) return;
        lastProgress = t;
        this.setState(bus, ctx, TaskState.TASK_STATE_WORKING, this.agentMessage(ctx, [textPart(text)]));
      },
    };
    entry.done = this.run(skill, schema.outputSchema, skillCtx, input);
    try {
      await this.status.track(async () => {
        this.setState(bus, ctx, TaskState.TASK_STATE_WORKING);
        const outcome = await withSpan(
          "sandman.a2a.task",
          { "sandman.skill": skillId, "sandman.task_id": ctx.taskId },
          () => entry.done,
        );
        if (outcome.error?.retryable) this.seen.delete(key);
        if (abort.signal.aborted) return;
        this.end(bus, ctx, outcome);
      });
    } finally {
      entry.settled = true;
      this.running.delete(ctx.taskId);
    }
  }

  private async run(
    skill: Skill,
    outputSchema: string,
    ctx: SkillContext,
    input: Record<string, unknown>,
  ): Promise<Outcome> {
    const fail = (error: SkillError): Outcome => ({ error, reject: false });
    let result: SkillResult;
    try {
      result = await skill.run(ctx, input);
    } catch (e) {
      if (e instanceof SkillError) return fail(e);
      if (e instanceof BudgetExceededError) return fail(new SkillError("BUDGET_EXCEEDED", e.message, false));
      console.error("skill failed", e);
      return fail(new SkillError("INTERNAL", `${(e as Error).name}: ${(e as Error).message}`, true));
    }
    try {
      this.contracts.validate(outputSchema, result.output);
    } catch (e) {
      if (e instanceof ContractError) return fail(new SkillError("INTERNAL", `invalid output: ${e.message}`, false));
      throw e;
    }
    if (result.eventType !== undefined) {
      if (!this.events || !result.eventData) {
        return fail(new SkillError("INTERNAL", "completion event without event sink", false));
      }
      try {
        await this.events.publish(result.eventType, String(input.projectId ?? ""), result.eventData, {
          correlationId: ctx.workflowId,
          causationId: ctx.messageId,
        });
      } catch (e) {
        console.error("publishing completion event failed", e);
        return fail(new SkillError("DEPENDENCY_UNAVAILABLE", (e as Error).message, true));
      }
    }
    return { output: result.output, reject: false };
  }

  async cancelTask(taskId: string, bus: ExecutionEventBus): Promise<void> {
    const task = this.running.get(taskId);
    if (task) {
      task.abort.abort();
      bus.publish(
        AgentEvent.statusUpdate({
          taskId,
          contextId: task.contextId,
          status: { state: TaskState.TASK_STATE_CANCELED, message: undefined, timestamp: timestamp() },
          metadata: undefined,
        }),
      );
    }
    bus.finished();
  }
}

interface CardRaw {
  supportedInterfaces: { url: string }[];
  capabilities?: { extensions?: { uri: string; params?: { skills?: Record<string, never>; plannedSkills?: unknown } }[] };
}

export interface ServeOptions {
  agentCardPath: string;
  skills: readonly Skill[];
  settings: Settings;
  events?: EventSink;
  contracts?: Contracts;
  status?: StatusTracker;
}

export function serve(options: ServeOptions): express.Express {
  const raw = JSON.parse(readFileSync(options.agentCardPath, "utf-8")) as CardRaw;
  const params = raw.capabilities?.extensions?.find((e) => e.uri === INVOCATION_EXTENSION)?.params;
  const schemas = (params?.skills ?? {}) as Record<string, { inputSchema: string; outputSchema: string }>;
  const planned = new Set(
    Array.isArray(params?.plannedSkills) ? (params.plannedSkills as string[]) : Object.keys(params?.plannedSkills ?? {}),
  );
  const missing = options.skills.filter((s) => !(s.id in schemas)).map((s) => s.id);
  if (missing.length > 0) throw new Error(`skills without schemas in ${options.agentCardPath}: ${missing.join(", ")}`);

  const authenticate: Authenticator = authenticatorFor(options.settings);
  const status = options.status ?? new StatusTracker(options.settings.serviceName);
  const executor = new Executor(
    new Map(options.skills.map((s) => [s.id, s])),
    schemas,
    planned,
    options.settings,
    options.contracts ?? new Contracts(options.settings.contractsDir),
    options.events,
    status,
  );
  const card = AgentCard.fromJSON(raw);
  const handler = new DefaultRequestHandler(card, new InMemoryTaskStore(), executor);
  const rpcPath = new URL(raw.supportedInterfaces[0]!.url).pathname || "/";

  const requireToken = (req: Request, res: Response, next: NextFunction): void => {
    if (authenticate(req.headers.authorization) === undefined) {
      res.status(401).json({ error: "missing or invalid token" });
      return;
    }
    next();
  };
  const app = express();
  app.use("/.well-known/agent-card.json", agentCardHandler({ agentCardProvider: handler }));
  app.use(
    rpcPath,
    requireToken,
    jsonRpcHandler({
      requestHandler: handler,
      userBuilder: async (req) => {
        const caller = authenticate(req.headers.authorization) ?? "";
        return { isAuthenticated: caller !== "", userName: caller };
      },
    }),
  );
  app.get("/v1/status", statusHandler(status, authenticate));
  return app;
}
