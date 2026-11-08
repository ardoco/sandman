import pytest
from sandman_extraction import classify, ingest

CSV = b"ID,Text\n1,x\n"
REQS = b"# REQ-1 A\nx\n# FR-12 B\ny\n"


@pytest.mark.parametrize(
    ("path", "head", "expected"),
    [
        ("model/a.repository", b"", ("architecture-model", "pcm-repository")),
        ("a.xmi", b"", ("architecture-model", "uml")),
        ("src/test/java/A.java", b"", ("test", None)),
        ("src/FooTest.java", b"", ("test", None)),
        ("pkg/test_a.py", b"", ("test", None)),
        ("web/a.spec.ts", b"", ("test", None)),
        ("src/main/A.java", b"", ("code", None)),
        ("tests/data.csv", CSV, ("requirements", "csv")),
        ("data.csv", b"a,b\n", ("other", None)),
        ("requirements/r.md", b"", ("requirements", None)),
        ("srs.pdf", b"", ("requirements", None)),
        ("notes.md", REQS, ("requirements", None)),
        ("notes.md", b"# REQ-1 A\n", ("other", None)),
        ("architecture.md", b"", ("architecture-doc", None)),
        ("adr/0001.md", b"", ("architecture-doc", "adr")),
        ("decisions/0001.txt", b"", ("architecture-doc", None)),
        ("docs/guide.md", b"", ("architecture-doc", None)),
        ("README.md", b"# hi\n", ("other", None)),
        ("logo.png", b"", ("other", None)),
    ],
)
def test_guess(path: str, head: bytes, expected: tuple[str, str | None]) -> None:
    assert classify.guess(path, head) == expected


def test_rule_beats_heuristic_and_fallback_is_distinguishable() -> None:
    m = ingest.Manifest(mode="partial", kindRules=[ingest.KindRule(glob="**/*.java", kind="other")], files=[])
    assert m.rule_of("a/B.java") == ("other", None)
    assert m.rule_of("a/B.py") is None
    assert m.kind_of("a/B.py") == ("other", None)
