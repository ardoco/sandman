"""User-declared knowledge file sandman-knowledge.yaml: intake validation and elements (T7.2)."""

from typing import Any

import yaml
from sandman_agent import ContractError, Contracts

SCHEMA = "knowledge.schema.json"
MAX_BYTES = 1024**2
DECL = "decl:"
MESSAGE_MAX = 300


class KnowledgeError(ValueError):
    """The file is invalid; the message starts with the JSON pointer of the error ("/" for the whole file)."""


def _pointer(path: str) -> str:
    return "/" if path in ("", "<root>") else "/" + path


def parse(content: bytes) -> Any:
    """YAML value of the file: size cap, UTF-8, no anchors or aliases, safe loading."""
    if len(content) > MAX_BYTES:
        raise KnowledgeError(f"/: file exceeds {MAX_BYTES} bytes")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        raise KnowledgeError("/: file is not UTF-8") from None
    try:
        if any(isinstance(t, yaml.AnchorToken | yaml.AliasToken) for t in yaml.scan(source, Loader=yaml.SafeLoader)):
            raise KnowledgeError("/: YAML anchors and aliases are not allowed")
        return yaml.safe_load(source)
    except (yaml.YAMLError, RecursionError) as e:
        raise KnowledgeError(f"/: invalid YAML: {str(e).splitlines()[0] if str(e) else type(e).__name__}") from None


def validate(content: bytes, contracts: Contracts) -> dict[str, Any]:
    """Parsed file after the schema and semantic checks; raises KnowledgeError."""
    data = parse(content)
    try:
        contracts.validate(SCHEMA, data)
    except ContractError as e:
        path, _, message = e.messages[0].partition(": ")
        raise KnowledgeError(f"{_pointer(path)}: {message[:MESSAGE_MAX]}") from None
    declared: set[str] = set()
    for i, element in enumerate(data.get("elements", [])):
        if element["id"] in declared:
            raise KnowledgeError(f"/elements/{i}/id: duplicate id {element['id']}")
        declared.add(element["id"])
    for section, keys in (("aliases", ("element",)), ("links", ("source", "target"))):
        for i, entry in enumerate(data.get(section, [])):
            for key in keys:
                ref = entry[key]
                if ref.startswith(DECL) and ref[len(DECL) :] not in declared:
                    raise KnowledgeError(f"/{section}/{i}/{key}: unknown element {ref}")
    return data
