"""Non-blocking product operations for MCP and other interactive hosts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_FILENAME = "optimization_job.json"
JOB_LOG_FILENAME = "optimization_job_worker.log"
_LOCK_FILENAME = ".optimization_job.lock"
_OPERATION_LOCK_FILENAME = ".optimization_workflow.lock"
_REQUEST_PREFIX = ".optimization_job_request-"
_PATH_ARGUMENTS = ("skill_path", "fixture")


class ProductOperationBusy(RuntimeError):
    """An operation is already modifying the workflow in this output_dir."""

    def __init__(
        self,
        output_dir: Path,
        *,
        job_id: str = "",
        worker_pid: int = 0,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.job_id = job_id
        self.worker_pid = worker_pid
        subject = f"optimization job {job_id}" if job_id else "optimization operation"
        super().__init__(
            f"{subject} is running for {self.output_dir}; only "
            "skilladam_status is allowed until it reaches a terminal state"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": "optimization_running",
            "state": "running",
            "output_dir": str(self.output_dir),
            "job_id": self.job_id,
            "worker_pid": self.worker_pid,
        }


def start_product_job(
    output_dir: Path,
    *,
    operation: str,
    arguments: Mapping[str, Any],
    review_mode: bool,
) -> dict[str, Any]:
    """Start a worker operation; allow one job per output directory at a time."""

    resolved = Path(output_dir).resolve()
    normalized_arguments = _normalize_job_arguments(
        arguments,
        output_dir=resolved,
    )
    resolved.mkdir(parents=True, exist_ok=True)
    with _job_lock(resolved):
        existing = _read_job(resolved, required=False)
        if existing is not None and existing.get("state") == "running":
            if _job_worker_alive(existing):
                return existing
            _finish_stale_job(resolved, existing)

    # Share the execution lease with synchronous prepare/continue/submit; use the
    # job metadata lock only for short transactions, not long model calls.
    with product_operation_lock(resolved):
        with _job_lock(resolved):
            existing = _read_job(resolved, required=False)
            if existing is not None and existing.get("state") == "running":
                if _job_worker_alive(existing):
                    return existing
                _finish_stale_job(resolved, existing)

            job_id = f"job_{uuid.uuid4().hex}"
            payload: dict[str, Any] = {
                "schema_version": "1",
                "job_id": job_id,
                "operation": operation,
                "state": "running",
                "output_dir": str(resolved),
                "worker_pid": 0,
                "started_at": _now(),
                "finished_at": "",
                "error": "",
                "result_state": "",
            }
            request_path = _request_path(resolved, job_id)
            _write_json(
                request_path,
                {
                    "schema_version": "1",
                    "job_id": job_id,
                    "arguments": normalized_arguments,
                    "review_mode": review_mode,
                },
            )
            _write_job(resolved, payload)
            try:
                process = _spawn_worker(resolved, job_id)
            except OSError as exc:
                payload.update(
                    state="failed",
                    finished_at=_now(),
                    error=f"{type(exc).__name__}: {exc}",
                )
                _write_job(resolved, payload)
                request_path.unlink(missing_ok=True)
                return payload
            payload["worker_pid"] = process.pid
            _write_job(resolved, payload)
            return payload


def _normalize_job_arguments(
    arguments: Mapping[str, Any],
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """Resolve host-relative paths before changing the worker cwd."""

    normalized = dict(arguments)
    normalized["output_dir"] = str(output_dir)
    for field in _PATH_ARGUMENTS:
        value = normalized.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = str(Path(value).resolve())
    return normalized


def product_job_status(output_dir: Path) -> dict[str, Any]:
    resolved = Path(output_dir).resolve()
    with _job_lock(resolved):
        payload = _read_job(resolved, required=True)
        assert payload is not None
        if payload.get("state") == "running" and not _job_worker_alive(
            payload
        ):
            payload = _finish_stale_job(resolved, payload)
        return payload


def ensure_product_job_idle(output_dir: Path) -> None:
    """Reject synchronous writes while a background job is alive."""

    resolved = Path(output_dir).resolve()
    with _job_lock(resolved):
        payload = _read_job(resolved, required=False)
        if payload is None or payload.get("state") != "running":
            return
        if not _job_worker_alive(payload):
            _finish_stale_job(resolved, payload)
            return
        job_id = str(payload.get("job_id", ""))
        worker_pid = payload.get("worker_pid")
        raise ProductOperationBusy(
            resolved,
            job_id=job_id,
            worker_pid=(
                worker_pid
                if isinstance(worker_pid, int) and not isinstance(worker_pid, bool)
                else 0
            ),
        )


def _spawn_worker(output_dir: Path, job_id: str) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "skilladam.product.jobs",
        "--worker",
        "--output-dir",
        str(output_dir),
        "--job-id",
        job_id,
    ]
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(output_dir),
    }
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        options["start_new_session"] = True
    log_path = output_dir / JOB_LOG_FILENAME
    with log_path.open("ab") as log:
        options["stdout"] = log
        options["stderr"] = log
        return subprocess.Popen(command, **options)


def _run_worker(output_dir: Path, job_id: str) -> int:
    request_path = _request_path(output_dir, job_id)
    result: Mapping[str, Any] | None = None
    error = ""
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, Mapping):
            raise ValueError("optimization job request must be an object")
        if request.get("schema_version") != "1":
            raise ValueError("optimization job request version is invalid")
        if request.get("job_id") != job_id:
            raise ValueError("optimization job request ID is invalid")
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            raise ValueError("optimization job arguments must be an object")
        review_mode = request.get("review_mode")
        if not isinstance(review_mode, bool):
            raise ValueError("optimization job review_mode must be boolean")

        from skilladam.product_mcp import _prepare_view

        with product_operation_lock(output_dir, wait_seconds=30.0):
            view = _prepare_view(
                output_dir,
                arguments,
                review_mode=review_mode,
                run_to_completion=True,
            )
        result = view.to_dict()
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)
    finally:
        _finish_worker_job(output_dir, job_id, result=result, error=error)
        request_path.unlink(missing_ok=True)
    return 1 if error else 0


def _finish_worker_job(
    output_dir: Path,
    job_id: str,
    *,
    result: Mapping[str, Any] | None,
    error: str,
) -> None:
    with _job_lock(output_dir):
        current = _read_job(output_dir, required=False)
        if current is None or current.get("job_id") != job_id:
            return
        finished = dict(current)
        finished["finished_at"] = _now()
        if error:
            finished["state"] = "failed"
            finished["error"] = error
        else:
            finished["state"] = "succeeded"
            session = result.get("session", {}) if result is not None else {}
            if isinstance(session, Mapping):
                finished["result_state"] = str(session.get("state", ""))
        _write_job(output_dir, finished)


def _finish_stale_job(
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    finished = dict(payload)
    finished.update(
        state="failed",
        finished_at=_now(),
        error=(
            "RuntimeError: optimization worker exited before recording a "
            "result; call skilladam_start to retry from persisted state"
        ),
    )
    _write_job(output_dir, finished)
    return finished


def _job_worker_alive(payload: Mapping[str, Any]) -> bool:
    pid = payload.get("worker_pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    return _process_alive(pid)


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def product_operation_lock(
    output_dir: Path,
    *,
    wait_seconds: float = 0.0,
) -> Iterator[None]:
    """Serialize cross-process operations that modify the optimization workflow."""

    resolved = Path(output_dir).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    path = resolved / _OPERATION_LOCK_FILENAME
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            try:
                os.write(descriptor, f"{token}\n".encode("ascii"))
            finally:
                os.close(descriptor)
            break
        except FileExistsError:
            if _remove_stale_operation_lock(path):
                continue
            if time.monotonic() >= deadline:
                raise ProductOperationBusy(
                    resolved,
                    worker_pid=_operation_lock_owner_pid(path),
                )
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            owner = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            owner = ""
        if owner == token:
            path.unlink(missing_ok=True)


def _remove_stale_operation_lock(path: Path) -> bool:
    try:
        owner = path.read_text(encoding="ascii").strip()
        pid_text, separator, _ = owner.partition(":")
        pid = int(pid_text) if separator else 0
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        try:
            # Treat a new lock as live during the brief window before its owner is written.
            if time.time() - path.stat().st_mtime < 5.0:
                return False
        except FileNotFoundError:
            return True
        pid = 0
    if pid > 0 and _process_alive(pid):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return True


def _operation_lock_owner_pid(path: Path) -> int:
    try:
        owner = path.read_text(encoding="ascii").strip()
        pid_text, separator, _ = owner.partition(":")
        return int(pid_text) if separator else 0
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return 0


@contextmanager
def _job_lock(output_dir: Path) -> Iterator[None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / _LOCK_FILENAME
    acquired = False
    for _ in range(500):
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            try:
                os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            finally:
                os.close(descriptor)
            acquired = True
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > 30
            except FileNotFoundError:
                continue
            if stale:
                path.unlink(missing_ok=True)
                continue
            time.sleep(0.01)
    if not acquired:
        raise RuntimeError("timed out acquiring optimization job lock")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _read_job(
    output_dir: Path,
    *,
    required: bool,
) -> dict[str, Any] | None:
    path = _job_path(output_dir)
    if not path.exists():
        if required:
            raise ValueError("output directory has no optimization job")
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "1":
        raise ValueError("optimization job file is invalid")
    return payload


def _write_job(output_dir: Path, payload: Mapping[str, Any]) -> None:
    _write_json(_job_path(output_dir), payload)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _request_path(output_dir: Path, job_id: str) -> Path:
    return output_dir / f"{_REQUEST_PREFIX}{job_id}.json"


def _job_path(output_dir: Path) -> Path:
    return output_dir / JOB_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    return parser


def _worker_entrypoint(argv: Sequence[str] | None = None) -> int:
    args = _build_worker_parser().parse_args(argv)
    return _run_worker(args.output_dir.resolve(), args.job_id)


__all__ = [
    "JOB_FILENAME",
    "JOB_LOG_FILENAME",
    "ProductOperationBusy",
    "ensure_product_job_idle",
    "product_operation_lock",
    "product_job_status",
    "start_product_job",
]


if __name__ == "__main__":
    raise SystemExit(_worker_entrypoint())
