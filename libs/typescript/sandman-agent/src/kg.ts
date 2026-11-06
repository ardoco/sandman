import { readFileSync } from "node:fs";
import neo4j from "neo4j-driver";
import { llmMode } from "./config.ts";
import { newRunId } from "./ids.ts";
import type { Usage } from "./llm.ts";
import { withSpan } from "./telemetry.ts";

export class MemoryLimitError extends Error {}

const MEMORY_LIMIT_CODE = "Neo.TransientError.General.MemoryPoolOutOfMemoryError";

export const isMemoryLimit = (e: unknown): boolean => (e as { code?: string } | null)?.code === MEMORY_LIMIT_CODE;

export interface KgTx {
  run(cypher: string, params?: Record<string, unknown>): Promise<{ records: { toObject(): Record<string, unknown> }[] }>;
}
export interface KgSession {
  executeRead<T>(work: (tx: KgTx) => Promise<T>): Promise<T>;
  executeWrite<T>(work: (tx: KgTx) => Promise<T>): Promise<T>;
  run(cypher: string, params?: Record<string, unknown>): Promise<{ records: { toObject(): Record<string, unknown> }[] }>;
  close(): Promise<void>;
}
export interface KgDriver {
  session(): KgSession;
  close(): Promise<void>;
}

// The driver retries TransientError; a plain error stops its retry loop.
function noRetryOnMemoryLimit<T>(fn: (tx: KgTx) => Promise<T>): (tx: KgTx) => Promise<T> {
  return async (tx) => {
    try {
      return await fn(tx);
    } catch (e) {
      if (isMemoryLimit(e)) throw new MemoryLimitError((e as Error).message, { cause: e });
      throw e;
    }
  };
}

export const now = (): string => new Date().toISOString().replace(/\.\d+Z$/, "Z");

export class KgClient {
  private readonly driver: KgDriver;

  constructor(uri: string, user?: string, password?: string, driver?: KgDriver) {
    this.driver =
      driver ??
      (neo4j.driver(uri, user ? neo4j.auth.basic(user, password ?? "") : undefined, {
        disableLosslessIntegers: true,
      }) as unknown as KgDriver);
  }

  close(): Promise<void> {
    return this.driver.close();
  }

  private async inSession<T>(op: string, cypher: string, fn: (s: KgSession) => Promise<T>): Promise<T> {
    return withSpan(`sandman.kg.${op}`, { "db.statement": cypher.slice(0, 200) }, async () => {
      const session = this.driver.session();
      try {
        return await fn(session);
      } finally {
        await session.close();
      }
    });
  }

  /**
   * Runs in a read transaction. Traverse rejects the words drop, set, delete, create, merge and remove in read
   * query text, also in string literals: pass them as parameters. A query whose rows enter an LLM prompt ends in
   * ORDER BY on a stable key.
   */
  async read(cypher: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>[]> {
    return this.inSession("read", cypher, (s) =>
      s.executeRead(
        noRetryOnMemoryLimit(async (tx) => (await tx.run(cypher, params)).records.map((r) => r.toObject())),
      ),
    );
  }

  async write(cypher: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>[]> {
    return this.inSession("write", cypher, (s) =>
      s.executeWrite(
        noRetryOnMemoryLimit(async (tx) => (await tx.run(cypher, params)).records.map((r) => r.toObject())),
      ),
    );
  }

  async writeTx<T>(fn: (tx: KgTx) => Promise<T>): Promise<T> {
    return this.inSession("write_tx", "", (s) => s.executeWrite(noRetryOnMemoryLimit(fn)));
  }

  async runAutocommit(cypher: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>[]> {
    return this.inSession("autocommit", cypher, async (s) => (await s.run(cypher, params)).records.map((r) => r.toObject()));
  }

  /** CREATE INDEX has no IF NOT EXISTS in Traverse: an "already exists" error is ignored. */
  async initSchema(cypherFile: string): Promise<void> {
    const code = readFileSync(cypherFile, "utf-8")
      .split("\n")
      .filter((line) => !line.trimStart().startsWith("//"))
      .join("\n");
    for (const statement of code.split(";").map((s) => s.trim()).filter(Boolean)) {
      try {
        await this.runAutocommit(statement);
      } catch (e) {
        if (!((e as Error).message ?? "").includes("already exists")) throw e;
      }
    }
  }
}

export interface RunStart {
  projectId: string;
  skill: string;
  tool: string;
  toolVersion: string;
  inputKey: string;
  model?: string;
  params?: string;
  configHash?: string;
  usedVersionIds?: readonly string[];
  workflowId?: string;
  taskId?: string;
  triggeredBy?: string;
  replayMode?: string;
  promptIds?: readonly string[];
}

export class RunRecorder {
  constructor(
    private readonly kg: Pick<KgClient, "write">,
    private readonly agent: string,
    private readonly agentVersion: string,
  ) {}

  async start(r: RunStart): Promise<string> {
    const id = newRunId();
    const props: Record<string, unknown> = {
      id, projectId: r.projectId, agent: this.agent, agentVersion: this.agentVersion, skill: r.skill, tool: r.tool,
      toolVersion: r.toolVersion, inputKey: r.inputKey, model: r.model, params: r.params, configHash: r.configHash,
      workflowId: r.workflowId, a2aTaskId: r.taskId, triggeredBy: r.triggeredBy, replayMode: r.replayMode ?? llmMode(),
      status: "running", startedAt: now(), promptIds: r.promptIds?.length ? [...r.promptIds] : undefined,
    };
    await this.kg.write(
      "CREATE (r:Run) SET r = $props " +
        "WITH r UNWIND $versions AS vid MATCH (v:ArtifactVersion {id: vid}) CREATE (r)-[:USED]->(v)",
      {
        props: Object.fromEntries(Object.entries(props).filter(([, v]) => v !== undefined)),
        versions: [...(r.usedVersionIds ?? [])],
      },
    );
    return id;
  }

  async complete(
    runId: string,
    usage?: Usage,
    status = "completed",
    resolvedModel?: string,
    mixedModels = false,
  ): Promise<void> {
    await this.kg.write(
      "MATCH (r:Run {id: $id}) SET r.status = $status, r.endedAt = $now, " +
        "r.inputTokens = $inp, r.outputTokens = $out, r.resolvedModel = $resolved, r.mixedModels = $mixed",
      {
        id: runId, status, now: now(), resolved: resolvedModel ?? null, mixed: mixedModels ? true : null,
        inp: usage ? neo4j.int(usage.inputTokens) : null, out: usage ? neo4j.int(usage.outputTokens) : null,
      },
    );
  }

  async fail(runId: string, error: string): Promise<void> {
    await this.kg.write("MATCH (r:Run {id: $id}) SET r.status = 'failed', r.error = $error, r.endedAt = $now", {
      id: runId, error, now: now(),
    });
  }

  async cleanupStale(inputKey: string): Promise<number> {
    const rows = await this.kg.write(
      "MATCH (r:Run {inputKey: $key}) WHERE r.status IN ['running', 'failed'] " +
        "OPTIONAL MATCH (r)-[:PRODUCED|DETECTED]->(f) WHERE f.runId = r.id " +
        "WITH r, collect(f) AS facts " +
        "SET r.status = 'failed', r.endedAt = coalesce(r.endedAt, $now) " +
        "WITH facts UNWIND facts AS f DETACH DELETE f RETURN count(f) AS deleted",
      { key: inputKey, now: now() },
    );
    return rows.length > 0 ? Number(rows[0]!.deleted) : 0;
  }
}
