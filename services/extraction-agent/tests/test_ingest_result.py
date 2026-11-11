"""Ingestion result shape without Docker: manifest classifierVersion, kinds and warnings stay out of the event."""

import json

import pytest
from pydantic import ValidationError
from sandman_extraction import ingest

ING = {"id": "ing_x", "projectId": "p", "seq": 1, "mode": "full", "unchangedCount": 0, "actor": "service:cli",
       "createdAt": "t", "kindCounts": json.dumps({"code": 2}), "warnings": ["w"]}  # fmt: skip


def test_manifest_classifier_version_is_optional_and_positive() -> None:
    assert ingest.Manifest(mode="full", files=[]).classifierVersion is None
    assert ingest.Manifest(mode="full", classifierVersion=3, files=[]).classifierVersion == 3
    with pytest.raises(ValidationError):
        ingest.Manifest(mode="full", classifierVersion=0, files=[])


def test_response_has_kinds_and_warnings_but_event_does_not() -> None:
    assert ingest.response(ING)["kinds"] == {"code": 2} and ingest.response(ING)["warnings"] == ["w"]
    data = ingest.event_data(ING)
    assert "kinds" not in data and "warnings" not in data and data["actor"] == "service:cli"


def test_response_without_kinds_for_old_ingestions() -> None:
    old = {k: v for k, v in ING.items() if k not in ("kindCounts", "warnings")}
    assert "kinds" not in ingest.response(old) and "warnings" not in ingest.response(old)
