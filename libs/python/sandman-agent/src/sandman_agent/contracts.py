"""Validation against the JSON Schemas in contracts/ (no network access)."""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_FILES = ("common.schema.json", "skills.schema.json", "events.schema.json")


class ContractError(Exception):
    """Instance does not match its contract; carries every validation message."""

    def __init__(self, ref: str, messages: list[str]) -> None:
        super().__init__(f"{ref}: " + "; ".join(messages))
        self.ref = ref
        self.messages = messages


class Contracts:
    """Loads the schemas once; refs like 'skills.schema.json#/$defs/X' or full $id URIs."""

    def __init__(self, contracts_dir: Path) -> None:
        schemas = [json.loads((contracts_dir / name).read_text(encoding="utf-8")) for name in SCHEMA_FILES]
        self._base = {name: s["$id"] for name, s in zip(SCHEMA_FILES, schemas, strict=True)}
        self._registry: Registry[Any] = Registry().with_resources(
            (s["$id"], Resource.from_contents(s)) for s in schemas
        )
        self._validators: dict[str, Draft202012Validator] = {}

    def _uri(self, ref: str) -> str:
        file, _, pointer = ref.partition("#")
        return str(self._base.get(file, file)) + (f"#{pointer}" if pointer else "")

    def validate(self, ref: str, instance: Any) -> None:
        """Raise ContractError with all messages if instance does not match ref."""
        uri = self._uri(ref)
        validator = self._validators.get(uri)
        if validator is None:
            validator = self._validators[uri] = Draft202012Validator({"$ref": uri}, registry=self._registry)
        messages = [
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(instance)
        ]
        if messages:
            raise ContractError(ref, messages)

    def is_valid(self, ref: str, instance: Any) -> bool:
        """True if instance matches ref."""
        try:
            self.validate(ref, instance)
        except ContractError:
            return False
        return True
