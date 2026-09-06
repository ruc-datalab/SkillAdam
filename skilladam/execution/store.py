"""Non-overwriting atomic storage for public execution artifacts."""

from __future__ import annotations

from collections.abc import Iterable
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class RunStore:
    """Manage one output root without destructive or implicit cleanup."""

    def __init__(self, root: Path, *, resume: bool = False) -> None:
        self.root = Path(root)
        self.resume = resume
        if self.root.is_symlink():
            raise ValueError("output root must not be a symlink")
        if self.root.exists():
            if not self.root.is_dir():
                raise ValueError("output root must be a directory")
            if not resume and any(self.root.iterdir()):
                raise ValueError(
                    "new run refuses a non-empty output directory"
                )
        else:
            if resume:
                raise ValueError("resume output root does not exist")
            parent = self.root.parent
            if not parent.is_dir():
                raise ValueError(
                    "output root parent must already exist"
                )
            self.root.mkdir()
        self._resolved_root = self.root.resolve(strict=True)

    def write_json(self, name: str | Path, value: Any) -> None:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        self._write(name, payload)

    def write_jsonl(
        self,
        name: str | Path,
        rows: Iterable[Any],
    ) -> None:
        lines = tuple(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for row in rows
        )
        payload = (
            ("\n".join(lines) + "\n") if lines else ""
        ).encode("utf-8")
        self._write(name, payload)

    def write_text(self, name: str | Path, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("text artifact value must be text")
        self._write(name, value.encode("utf-8"))

    def _write(self, name: str | Path, payload: bytes) -> None:
        target = self._managed_target(name)
        if target.exists():
            if not target.is_file():
                raise FileExistsError(
                    f"managed artifact already exists: {name}"
                )
            existing = target.read_bytes()
            if self.resume and existing == payload:
                return
            qualifier = "differs" if self.resume else "already exists"
            raise FileExistsError(
                f"managed artifact {qualifier}: {name}"
            )

        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _managed_target(self, name: str | Path) -> Path:
        raw_name = str(name)
        candidate = Path(name)
        if (
            not raw_name
            or raw_name == "."
            or "\\" in raw_name
            or candidate.is_absolute()
            or ".." in candidate.parts
        ):
            raise ValueError(
                "managed artifact name must be a safe relative path"
            )
        if candidate.parts in {(), (".",)}:
            raise ValueError(
                "managed artifact name must be a safe relative path"
            )

        current = self.root
        for part in candidate.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    "managed artifact path must not contain a symlink"
                )
            if current.exists():
                if not current.is_dir():
                    raise ValueError(
                        "managed artifact parent must be a directory"
                    )
            else:
                current.mkdir()

        target = self.root.joinpath(*candidate.parts)
        if target.is_symlink():
            raise ValueError(
                "managed artifact target must not be a symlink"
            )
        resolved_parent = target.parent.resolve(strict=True)
        try:
            resolved_parent.relative_to(self._resolved_root)
        except ValueError as exc:
            raise ValueError(
                "managed artifact path escapes output root"
            ) from exc
        return target


__all__ = ["RunStore"]
