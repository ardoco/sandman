import { describe, expect, it } from "vitest";
import { classSummary, classView, ClassRow, editList, initialOpen, needsReview, setEdit, unreviewedMessage } from "./logic";

const row = (path: string, type: "dir" | "file", kind: string, extra: Partial<ClassRow> = {}): ClassRow => ({
  path, name: path.split("/").pop()!, type, kind, files: 1, weak: false, source: null, reason: null, depth: path.split("/").length - 1, ...extra,
});
const rows = [
  row("docs", "dir", "mixed", { files: 3, weak: true }),
  row("docs/a.md", "file", "documentation", { weak: true }),
  row("docs/legacy", "dir", "documentation", { files: 2, weak: true }),
  row("docs/legacy/b.md", "file", "documentation", { weak: true }),
  row("docs/legacy/c.md", "file", "documentation", { weak: true }),
  row("src", "dir", "code", { files: 1 }),
  row("src/A.java", "file", "code"),
];
const none = new Map<string, string>();

describe("classification render model", () => {
  it("opens mixed folders only and folds uniform ones", () => {
    const open = initialOpen(rows);
    expect([...open]).toEqual(["docs"]);
    expect(classView(rows, none, open).map((r) => r.path)).toEqual(["docs", "docs/a.md", "docs/legacy", "src"]);
    expect(classView(rows, none, new Set()).map((r) => r.path)).toEqual(["docs", "src"]);
  });

  it("applies folder edits to everything below and recomputes folder kinds", () => {
    const e = setEdit(none, rows, "docs/legacy", "other");
    const v = Object.fromEntries(classView(rows, e, new Set(["docs", "docs/legacy"])).map((r) => [r.path, r]));
    expect(v["docs/legacy/b.md"].effKind).toBe("other");
    expect(v["docs"].effKind).toBe("mixed");
    expect(v["docs/legacy"].stillWeak).toBe(false);
    expect(v["docs/a.md"].edited).toBe(false);
    const all = setEdit(e, rows, "docs", "other");
    expect(editList(all)).toEqual([{ path: "docs", kind: "other" }]);
    expect(classView(rows, all, new Set(["docs"])).find((r) => r.path === "docs")!.effKind).toBe("other");
  });

  it("drops an edit that restores the original kind and keeps overrides under an edited folder", () => {
    expect(setEdit(setEdit(none, rows, "src/A.java", "test"), rows, "src/A.java", "code").size).toBe(0);
    const e = setEdit(setEdit(none, rows, "docs", "other"), rows, "docs/a.md", "documentation");
    expect(editList(e)).toEqual([{ path: "docs", kind: "other" }, { path: "docs/a.md", kind: "documentation" }]);
  });

  it("filters to weak or changed rows and searches, keeping parents", () => {
    const open = initialOpen(rows);
    expect(classView(rows, none, open, "weak").map((r) => r.path)).toEqual(["docs", "docs/a.md", "docs/legacy", "docs/legacy/b.md", "docs/legacy/c.md"]);
    const e = setEdit(none, rows, "src/A.java", "test");
    expect(classView(rows, e, open, "changed").map((r) => r.path)).toEqual(["src", "src/A.java"]);
    expect(classView(rows, none, open, "all", "C.MD").map((r) => r.path)).toEqual(["docs", "docs/legacy", "docs/legacy/c.md"]);
  });
});

describe("classification review text", () => {
  const files = [
    { path: "docs/a.md", kind: "documentation", source: "heuristic", reason: "", weak: true },
    { path: "docs/legacy/b.md", kind: "documentation", source: "heuristic", reason: "", weak: true },
    { path: "src/A.java", kind: "code", source: "heuristic", reason: "", weak: false },
  ];
  it("names file and folder counts", () => {
    expect(unreviewedMessage(files)).toBe("2 files in 2 folders were not reviewed.");
    expect(unreviewedMessage(files.slice(0, 1))).toBe("1 file in 1 folder was not reviewed.");
  });
  it("detects an open review and summarizes", () => {
    const scan = { root: "/r", classifierVersion: 1, fileCount: 3, kinds: {}, ignored: {}, review: { weak: 2, reviewed: false, fingerprint: "x", reviewedFingerprint: null }, rows: [], files, warnings: [] };
    expect(needsReview(scan)).toBe(true);
    expect(needsReview({ review: { ...scan.review, reviewed: true } })).toBe(false);
    expect(classSummary(scan)).toBe("3 files, classifier v1. Review open: 2 files in 2 folders were not reviewed.");
  });
});
