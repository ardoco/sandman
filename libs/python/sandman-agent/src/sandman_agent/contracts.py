"""Validation against the JSON Schemas in contracts/ (no network access)."""

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_FILES = ("common.schema.json", "skills.schema.json", "events.schema.json", "knowledge.schema.json")


class ContractError(Exception):
    """Instance does not match its contract; carries every validation message."""

    def __init__(self, ref: str, messages: list[str]) -> None:
        super().__init__(f"{ref}: " + "; ".join(messages))
        self.ref = ref
        self.messages = messages


class Contracts:
    """Loads the schemas once; refs like 'skills.schema.json#/$defs/X' or full $id URIs."""

    def __init__(self, contracts_dir: Path) -> None:
        names = [*SCHEMA_FILES, *(f"config/{p.name}" for p in sorted((contracts_dir / "config").glob("*.schema.json")))]
        schemas = [json.loads((contracts_dir / name).read_text(encoding="utf-8")) for name in names]
        self._base = {name: s["$id"] for name, s in zip(names, schemas, strict=True)}
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

    def load_config(self, schema: str, path: Path) -> Any:
        """YAML file validated against contracts/config/<schema>.schema.json (e.g. 'query-plugins'); empty file = {}."""
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        try:
            self.validate(f"config/{schema}.schema.json", data)
        except ContractError as e:
            raise ContractError(f"{path}", e.messages) from None
        return data
