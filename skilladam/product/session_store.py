"""Product session checkpoints resumable across processes."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


class SessionStore:
    """Atomically replace session JSON using a temporary file in the same directory."""

    def __init__(
        self,
        output_dir: Path,
        *,
        filename: str = "optimization_session.json",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / filename

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("session checkpoint must contain a JSON object")
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        temporary = self.path.with_name(
            f".{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["SessionStore"]
