import json
from pathlib import Path

import httpx
import pytest
from sandman_cli.__main__ import main
from sandman_cli.evaluation import formats
from sandman_cli.evaluation.compare import compare, identical


def row(src_kind: str, idx: int | None, src_key: str, tgt_kind: str, tgt_key: str, tgt_path: str) -> dict[str, object]:
    return {
        "sourceKey": src_key,
        "sourceIndex": idx,
        "sourceKind": src_kind,
        "sourcePath": "docs/sad.md",
        "targetKey": tgt_key,
        "targetKind": tgt_kind,
        "targetPath": tgt_path,
    }


ROWS = [
    row("architecture-doc", 0, "art_d#sec:/abc", "architecture-model", "art_m#Comp1", "model/a.repository"),
    row("architecture-doc", 2, "art_d#sec:/def", "code", "art_c#file", "src/main/A.java"),
    row("architecture-doc", None, "art_d#file", "code", "art_c#file", "src/main/B.java"),
]


def test_roundtrip_and_sort() -> None:
    links = {("b", "10"), ("b", "2"), ("a", "3")}
    text = formats.write_links("ardoco-sad-sam", links)
    assert text.splitlines() == ["modelElementID,sentence", "a,3", "b,2", "b,10"]
    assert formats.read_links("ardoco-sad-sam", text) == links


def test_rows_to_links() -> None:
    assert formats.rows_to_links("ardoco-sad-sam", ROWS) == {("Comp1", "1")}
    assert formats.rows_to_links("ardoco-sad-code", ROWS, "src/main") == {("3", "A.java")}


def test_fetch_rows_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formats, "PAGE", 2)
    columns = list(ROWS[0])
    skips: list[int] = []

    def cypher(body: dict[str, object]) -> dict[str, object]:
        skip = body["params"]["skip"]  # type: ignore[index]
        skips.append(skip)
        return {"columns": columns, "rows": [list(r.values()) for r in ROWS[skip : skip + 2]]}

    assert formats.fetch_rows(cypher, "p", None) == ROWS
    assert skips == [0, 2]


def test_compare_and_identical() -> None:
    gold = {("m", str(i)) for i in range(20)}
    a = set(list(gold)[:15]) | {("m", "99")}
    b = gold
    r = compare(gold, a, {"b": b}, published_f1=0.8, resamples=200)
    assert r["comparisons"]["b"]["mcnemar"]["onlyB"] == 6
    assert r["published"]["delta"] == pytest.approx(r["a"]["f1"] - 0.8)
    assert identical(a, a)["equal"] and not identical(a, b)["equal"]


def test_cli_export_and_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SANDMAN_TOKEN", "t")
    monkeypatch.setenv("SANDMAN_URL", "http://qs:8080")
    columns = list(ROWS[0])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query/cypher"
        return httpx.Response(200, json={"columns": columns, "rows": [list(r.values()) for r in ROWS]})

    assert (
        main(["export", "tracelinks", "--project", "pj", "--format", "ardoco-sad-sam"], httpx.MockTransport(handler))
        == 0
    )
    out = capsys.readouterr().out
    assert out == "modelElementID,sentence\nComp1,1\n"
    gold = tmp_path / "gold.csv"
    gold.write_text(out)
    assert main(["eval", "compare", "--format", "ardoco-sad-sam", "--gold", str(gold), "--a", str(gold)]) == 0
    assert json.loads(capsys.readouterr().out)["a"]["f1"] == 1.0
