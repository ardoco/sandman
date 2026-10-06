"""Content-addressed artifact store: /<root>/<projectId>/<sha256>."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO

_PROJECT = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_URI = re.compile(r"^artifact://([a-z0-9][a-z0-9-]{1,62})/([0-9a-f]{64})$")


class ArtifactStore:
    """Writes blobs atomically; names derive only from validated ids, never from user paths."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, project_id: str, data: bytes) -> tuple[str, str]:
        """Store data; return (sha256, storage uri). Idempotent."""
        if not _PROJECT.match(project_id):
            raise ValueError(f"invalid project id {project_id!r}")
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / project_id / digest
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                tmp.write(data)
            os.replace(tmp.name, target)
        return digest, f"artifact://{project_id}/{digest}"

    def path_for(self, uri: str) -> Path:
        """Filesystem path of a storage uri."""
        m = _URI.match(uri)
        if not m:
            raise ValueError(f"invalid artifact uri {uri!r}")
        return self.root / m.group(1) / m.group(2)

    def open(self, uri: str) -> BinaryIO:
        """Open a stored blob for reading."""
        return self.path_for(uri).open("rb")
