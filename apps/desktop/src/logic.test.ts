import { describe, expect, it } from "vitest";
import { ASK_HISTORY_MAX, askErrorMessage, asOfSummary, buildAskBody, pushHistory, availableFormats, exportFileName, exportSummary, columnsLayout, connectSummary, localProject, loginFields, loginMessage, prefillUrl, defaultKeyLabel, KEY_KINDS, kindName, maskKey, dashboardUrl, filterLinks, worstSeverity, lastLines, latestWorkflow, needsCause, problemMessage, reviewable, slugify, tokenMessage, validSlug, windowTitle, workflowActive, workflowProgress } from "./logic";

describe("logic", () => {
  it("validates project slugs like the server", () => {
    for (const ok of ["ab", "a-1", "0x"]) expect(validSlug(ok)).toBe(true);
    for (const bad of ["a", "-a", "A1", "a_b", "a b", "a".repeat(64)]) expect(validSlug(bad)).toBe(false);
    expect(validSlug(slugify("My Demo  Project!"))).toBe(true);
    expect(slugify("My Demo  Project!")).toBe("my-demo-project");
  });

  it("prefers problem detail, then errors, then title", () => {
    expect(problemMessage(422, { title: "T", detail: "reason is required" })).toBe("reason is required");
    expect(problemMessage(400, { title: "T", errors: ["a", "b"] })).toBe("a; b");
    expect(problemMessage(409, { title: "Conflict" })).toBe("Conflict");
    expect(problemMessage(502, "bad gateway")).toBe("bad gateway");
    expect(problemMessage(500, null)).toBe("HTTP 500");
  });

  it("maps workflow steps to progress", () => {
    const wf = {
      status: "RUNNING",
      steps: [
        { skill: "extract", agent: "extraction-agent", status: "SUCCEEDED", attempts: 1 },
        { skill: "tlr", agent: "tlr-agent", status: "RUNNING", attempts: 1, activity: { text: "linking" } },
        { skill: "detect", agent: "inconsistency-agent", status: "PENDING", attempts: 0 },
      ],
    };
    expect(workflowProgress(wf)).toEqual({ done: 1, total: 3, percent: 33, label: "tlr (tlr-agent): linking", finished: false });
    expect(workflowProgress({ status: "SUCCEEDED", steps: [] })).toMatchObject({ percent: 100, finished: true });
    expect(workflowProgress({ status: "BLOCKED" }).finished).toBe(true);
  });

  it("helpers", () => {
    expect(latestWorkflow([{ createdAt: "2026-01-01" }, { createdAt: "2026-02-01" }])?.createdAt).toBe("2026-02-01");
    expect(needsCause("DEFERRED")).toBe(true);
    expect(needsCause("OPEN")).toBe(false);
    expect(dashboardUrl("http://127.0.0.1:8080")).toBe("http://127.0.0.1:8090");
  });

  it("explains token check results", () => {
    expect(tokenMessage(200, {})).toBe("Token accepted.");
    expect(tokenMessage(401, null)).toContain("rejected");
    expect(tokenMessage(403, { detail: "password change required" })).toContain("Password change required");
    expect(tokenMessage(403, { detail: "not allowed" })).toBe("not allowed");
    expect(tokenMessage(500, null)).toBe("HTTP 500");
  });

  it("small view helpers", () => {
    expect(workflowActive("RUNNING") && workflowActive("PENDING")).toBe(true);
    expect(workflowActive("SUCCEEDED")).toBe(false);
    expect(reviewable("explicit-reference")).toBe(false);
    expect(reviewable("user-declared")).toBe(false);
    expect(reviewable("lissa")).toBe(true);
    expect(windowTitle("Demo", "running (ready)")).toBe("Demo - running (ready) - SANDMAN");
    expect(windowTitle("Demo", null)).toBe("Demo - SANDMAN");
    expect(lastLines(["a", "b", "c"], 2)).toBe("b\nc");
  });

  it("lays out one column per label with indent by depth", () => {
    const m = columnsLayout([
      { id: "r1", label: "Requirement" },
      { id: "r2", label: "Requirement" },
      { id: "m1", label: "ModelElement" },
      { id: "m2", label: "ModelElement", parentId: "m1" },
      { id: "x", label: "Other" },
    ]);
    expect(m.get("r2")).toMatchObject({ column: 0, x: 0, y: 64 });
    expect(m.get("m1")).toMatchObject({ column: 2, x: 720, y: 0 });
    expect(m.get("m2")).toMatchObject({ x: 744, y: 64, depth: 1 });
    expect(m.has("x")).toBe(false);
  });

  it("filters links by status, approach and confidence", () => {
    const links = [
      { sourceId: "a", targetId: "b", approach: "lissa", confidence: 0.9, status: "confirmed" },
      { sourceId: "a", targetId: "c", approach: "tlr", confidence: 0.4, status: "candidate" },
      { sourceId: "a", targetId: "d", approach: "lissa", confidence: 0.8, status: "rejected" },
    ];
    const f = { statuses: new Set(["confirmed", "candidate"]), approaches: new Set<string>(), minConfidence: 0.5 };
    expect(filterLinks(links, f).map((l) => l.targetId)).toEqual(["b"]);
    expect(filterLinks(links, { ...f, minConfidence: 0, approaches: new Set(["tlr"]) }).map((l) => l.targetId)).toEqual(["c"]);
  });

  it("finds the worst open severity", () => {
    const fs = [
      { severity: "warning", status: "OPEN", involved: [{ id: "a" }] },
      { severity: "error", status: "RESOLVED", involved: [{ id: "a" }] },
      { severity: "info", status: "REOPENED", involved: [{ id: "a" }, { id: "b" }] },
    ];
    expect(worstSeverity(fs, "a")).toEqual({ severity: "warning", open: 2 });
    expect(worstSeverity(fs, "a", false)).toEqual({ severity: "error", open: 2 });
    expect(worstSeverity(fs, "z")).toEqual({ severity: null, open: 0 });
  });
});

describe("key store helpers", () => {
  it("masks secrets and shows URLs", () => {
    expect(maskKey("OPENAI_API_KEY", "1234")).toBe("••••1234");
    expect(maskKey("OPENAI_API_KEY", "")).toBe("••••");
    expect(maskKey("SELFHOSTED_LLM_URL", "https://llm.example")).toBe("https://llm.example");
  });
  it("builds the default label", () => {
    expect(defaultKeyLabel("Demo", "OPENROUTER_API_KEY")).toBe("Demo OpenRouter key");
    expect(defaultKeyLabel("  ", "SELFHOSTED_LLM_URL")).toBe("project self-hosted URL");
    expect(KEY_KINDS.map(kindName)).toHaveLength(4);
  });

  it("summarizes a connect result", () => {
    const base = { username: "ann", added: [], updated: [], skipped: [], warning: null };
    expect(connectSummary({ ...base, added: ["a1"], updated: ["b2"], skipped: [{ id: "c3", reason: "a local project with this id exists" }], warning: "Use https." })).toEqual([
      "Connected as ann.", "Added: a1.", "Updated: b2.", "Skipped c3: a local project with this id exists.", "Use https."]);
    expect(connectSummary(base)).toEqual(["Connected as ann.", "This token can read no project."]);
  });

  it("lists the connect form fields per login mode and maps login errors", () => {
    expect(loginFields("password")).toEqual(["URL", "Username", "Password"]);
    expect(loginFields("token")).toEqual(["URL", "Token"]);
    expect(loginMessage(401, null)).toContain("Wrong username or password");
    expect(loginMessage(429, { detail: "too many failed logins" })).toContain("15 minutes");
    expect(loginMessage(403, { detail: "password change required" })).toContain("Password change required");
    expect(loginMessage(403, { detail: "not allowed" })).toBe("not allowed");
    expect(loginMessage(500, null)).toBe("HTTP 500");
  });

  it("picks the prefill URL and the local project from the list sorted by last use", () => {
    const list = [{ mode: "local", url: "http://127.0.0.1:8080", id: "l1" }, { mode: "remote", url: "https://a.example", id: "r1" }, { mode: "remote", url: "https://b.example", id: "r2" }];
    expect(prefillUrl(list)).toBe("https://a.example");
    expect(prefillUrl([list[0]])).toBe("");
    expect(localProject(list)?.id).toBe("l1");
    expect(localProject(list.slice(1))).toBeUndefined();
  });

  it("disables the benchmark formats without a CLI command and checkout", () => {
    const off = (p: object, what: "tracelinks" | "findings" = "tracelinks") => availableFormats(p, what).filter((f) => f.disabled).map((f) => f.format);
    expect(off({})).toEqual(["ardoco-sad-sam", "ardoco-sad-code", "ardoco-sam-code", "lissa"]);
    expect(off({ cliCommand: "  " })).toHaveLength(4);
    expect(off({ cliCommand: "sandman" })).toEqual([]);
    expect(off({ checkout: "/co" })).toEqual([]);
    expect(availableFormats({}, "findings").map((f) => f.format)).toEqual(["csv", "json"]);
    expect(availableFormats({}, "tracelinks").slice(0, 2).every((f) => !f.disabled)).toBe(true);
  });

  it("builds the export file name", () => {
    expect(exportFileName("demo", "tracelinks", "csv")).toBe("demo-tracelinks.csv");
    expect(exportFileName("demo", "tracelinks", "json", "ardoco")).toBe("demo-tracelinks-ardoco.json");
    expect(exportFileName("demo", "tracelinks", "ardoco-sad-code", "My/App 1")).toBe("demo-tracelinks-my-app-1.csv");
    expect(exportFileName("demo", "findings", "json", "")).toBe("demo-findings.json");
  });

  it("summarizes an export", () => {
    expect(exportSummary(1234, "/tmp/a.csv")).toBe("Saved 1,234 rows to /tmp/a.csv");
  });

  it("builds the Ask body with at most one earlier state", () => {
    const f = { question: " untraced requirements ", strategy: "auto", cypherOnly: false, when: "none" as const, time: "2026-10-02T12:00", revision: "abc" };
    expect(buildAskBody("demo", f)).toEqual({ projectId: "demo", question: "untraced requirements" });
    expect(buildAskBody("demo", { ...f, strategy: "explore", cypherOnly: true })).toMatchObject({ strategy: "explore", mode: "cypher-only" });
    const time = buildAskBody("demo", { ...f, when: "time" });
    expect(time.asOf).toBe(new Date("2026-10-02T12:00").toISOString());
    expect(time.asOf).toMatch(/Z$/);
    expect(time).not.toHaveProperty("asOfRevision");
    const rev = buildAskBody("demo", { ...f, when: "revision" });
    expect(rev.asOfRevision).toBe("abc");
    expect(rev).not.toHaveProperty("asOf");
    expect(buildAskBody("demo", { ...f, when: "time", time: "" })).not.toHaveProperty("asOf");
  });

  it("keeps the last 10 distinct questions, newest first", () => {
    let h: string[] = [];
    for (let i = 0; i < 12; i++) h = pushHistory(h, `question ${i}`);
    expect(h).toHaveLength(ASK_HISTORY_MAX);
    expect(h[0]).toBe("question 11");
    expect(pushHistory(h, " question 5 ")[0]).toBe("question 5");
    expect(pushHistory(h, "question 5").filter((x) => x === "question 5")).toHaveLength(1);
    expect(pushHistory(h, "  ")).toBe(h);
  });

  it("maps Ask errors to clear texts", () => {
    expect(askErrorMessage(422, "older than retention")).toContain("older than retention");
    expect(askErrorMessage(422, "x")).toMatch(/cannot answer/);
    expect(askErrorMessage(501, "x")).toMatch(/not enabled/);
    expect(askErrorMessage(409, "x")).toMatch(/embeddings/);
    expect(askErrorMessage(401, "x")).toBe(tokenMessage(401, {}));
    expect(askErrorMessage(500, "boom")).toBe("boom");
  });

  it("summarizes the state that was read", () => {
    expect(asOfSummary({ ingestionSeq: 0 })).toMatch(/no ingestion/);
    expect(asOfSummary({ ingestionSeq: 3, ingestedAt: "2026-10-02T10:00:00Z", sourceRevision: "abc" })).toBe("State read: ingestion 3, ingested at 2026-10-02T10:00:00Z, revision abc");
    expect(asOfSummary({ ingestionSeq: 3 })).toBe("State read: ingestion 3");
  });
});
