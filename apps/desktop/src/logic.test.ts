import { describe, expect, it } from "vitest";
import { dashboardUrl, latestWorkflow, needsCause, problemMessage, slugify, validSlug, workflowProgress } from "./logic";

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
});
