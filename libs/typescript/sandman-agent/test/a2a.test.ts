import { readFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { INVOCATION_EXTENSION, serve, type Skill } from "../src/a2a.ts";
import { DEFAULT_CONTRACTS_DIR, type Settings } from "../src/config.ts";
import { buildEvent, type Event, type EventSink } from "../src/events.ts";
import { Contracts } from "../src/contracts.ts";
import { sha256Hex } from "../src/ids.ts";

/* eslint-disable @typescript-eslint/no-explicit-any */
const vectors: any = JSON.parse(readFileSync(join(DEFAULT_CONTRACTS_DIR, "conformance/vectors.json"), "utf-8"));
const contracts = new Contracts(DEFAULT_CONTRACTS_DIR);

const published: Event[] = [];
const sink: EventSink = {
  publish: async (type, subject, data, options) => {
    const event = buildEvent(contracts, "tlr-agent", type, subject, data, options);
    published.push(event);
    return event;
  },
};

let runs = 0;
const skill: Skill = {
  id: "recover-trace-links",
  run: async (_ctx, input) => {
    runs++;
    const run = {
      approach: "lissa", approachVersion: "1.0.0", runId: "run_0123456789abcdef0123456789abcdef",
      status: "completed", linkCount: 0,
    };
    return {
      output: { runs: [run] },
      eventType: "sandman.tracelinks.updated",
      eventData: {
        projectId: input.projectId, sourceVersionIds: input.sourceVersionIds,
        targetVersionIds: input.targetVersionIds, runs: [run],
      },
    };
  },
};

const settings: Settings = {
  serviceName: "tlr-agent", contractsDir: DEFAULT_CONTRACTS_DIR,
  serviceVerify: { orchestrator: sha256Hex("secret") },
};

let server: ReturnType<ReturnType<typeof serve>["listen"]>;
let base = "";

beforeAll(async () => {
  const app = serve({
    agentCardPath: join(DEFAULT_CONTRACTS_DIR, "agent-cards/tlr-agent.json"),
    skills: [skill], settings, events: sink, contracts,
  });
  await new Promise<void>((resolve) => {
    server = app.listen(0, "127.0.0.1", () => resolve());
  });
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => new Promise<void>((resolve) => server.close(() => resolve())));

const send = async (message: unknown, token: string | null = "secret"): Promise<Response> =>
  fetch(`${base}/a2a`, {
    method: "POST",
    headers: {
      "content-type": "application/json", "A2A-Version": "1.0", "A2A-Extensions": INVOCATION_EXTENSION,
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "SendMessage", params: { message } }),
  });

const taskOf = async (res: Response): Promise<any> => {
  const body: any = await res.json();
  expect(body.error, JSON.stringify(body)).toBeUndefined();
  return body.result.task ?? body.result;
};
const errorOf = (task: any): any => task.status.message?.metadata?.["sandman.error"];

describe("a2a skill server", () => {
  const cases: any[] = vectors.skillInvocation.cases;

  it.each(cases.filter((c) => c.expectedState === "TASK_STATE_REJECTED"))("$name", async (c) => {
    const task = await taskOf(await send(c.message));
    expect(task.status.state).toBe("TASK_STATE_REJECTED");
    expect(errorOf(task).code).toBe(c.expectedErrorCode);
  });

  it("completes a valid invocation with one result artifact and one completion event", async () => {
    const c = cases.find((x) => x.name === "valid input is accepted");
    published.length = 0;
    const task = await taskOf(await send(c.message));
    expect(task.status.state).toBe("TASK_STATE_COMPLETED");
    expect(task.artifacts[0].name).toBe("result");
    expect(published).toHaveLength(1);
    expect(published[0]!.type).toBe("sandman.tracelinks.updated");
  });

  it("answers a repeated messageId without running the skill again", async () => {
    const c = cases.find((x) => x.name === "valid input is accepted");
    const before = runs;
    published.length = 0;
    const task = await taskOf(await send({ ...c.message, messageId: "m-repeat" }));
    await taskOf(await send({ ...c.message, messageId: "m-repeat" }));
    expect(task.status.state).toBe("TASK_STATE_COMPLETED");
    expect(runs).toBe(before + 1);
    expect(published).toHaveLength(1);
  });

  it("rejects requests without a valid token", async () => {
    const c = cases[0];
    expect((await send(c.message, null)).status).toBe(401);
    expect((await send(c.message, "wrong")).status).toBe(401);
  });

  it("serves the agent card and the status route", async () => {
    const card: any = await (await fetch(`${base}/.well-known/agent-card.json`)).json();
    expect(card.name).toBe("sandman-tlr-agent");
    const status = await fetch(`${base}/v1/status`, { headers: { authorization: "Bearer secret" } });
    expect(status.status).toBe(200);
    expect((await fetch(`${base}/v1/status`)).status).toBe(401);
  });
});
