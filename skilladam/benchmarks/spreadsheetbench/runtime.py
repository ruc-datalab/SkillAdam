"""Explicit execution boundaries for SpreadsheetBench-generated Python."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from typing import Any


_IMAGE_REFERENCE_RE = re.compile(
    r"(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}"
)
_SAFE_CASE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_PATH_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:INPUT_PATH|OUTPUT_PATH)\s*=.*$",
    re.MULTILINE,
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:])/(?:[^\s,;'\"\]\)]+)"
)
_APPROVED_ENGINES = frozenset({"docker", "podman"})
_SECURITY_PROFILES = frozenset({
    "engine-security-opt",
    "setpriv-wrapper",
})
_ENGINE_ENVIRONMENT_NAMES = (
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONTEXT",
    "CONTAINER_HOST",
    "CONTAINER_CONNECTION",
)
_MAX_ERROR_CHARACTERS = 3000
_DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_SET_PRIV_ENVIRONMENT = (
    "TMPDIR=/output/tmp",
    "OPENBLAS_NUM_THREADS=1",
    "OMP_NUM_THREADS=1",
    "MKL_NUM_THREADS=1",
    "NUMEXPR_NUM_THREADS=1",
)
_SET_PRIV_PROBE = textwrap.dedent(
    """
    import errno
    import os
    from pathlib import Path

    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    assert status["NoNewPrivs"].strip() == "1"
    for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert int(status[name].strip(), 16) == 0
    assert os.getuid() == os.geteuid() == 65534
    assert os.getgid() == os.getegid() == 65534
    assert {path.name for path in Path("/sys/class/net").iterdir()} <= {"lo"}
    try:
        Path("/tmp/skilladam-rootfs-write-probe").write_bytes(b"probe")
    except OSError as exc:
        assert exc.errno == errno.EROFS
    else:
        raise AssertionError("sandbox root filesystem is writable")
    """
).strip()


class SpreadsheetSandboxRuntimeError(RuntimeError):
    """One path-free SpreadsheetBench sandbox infrastructure failure."""


@dataclass(frozen=True, slots=True)
class SandboxExecutionResult:
    """Portable result plus retained host-only artifact locations."""

    ok: bool
    artifact_dir: Path
    output_path: Path | None
    error_type: str = ""
    error_message: str = ""
    returncode: int | None = None


class OCISandboxExecutor:
    """Construct and invoke one fixed Docker/Podman sandbox command."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        engine: str,
        image_reference: str,
        timeout_seconds: float,
        security_profile: str = "engine-security-opt",
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
        command_runner: Callable[..., Any] = subprocess.run,
        executable_finder: Callable[[str], str | None] = shutil.which,
        host_environ: Mapping[str, str] | None = None,
    ) -> None:
        if engine not in _APPROVED_ENGINES:
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox engine must be docker or podman"
            )
        if security_profile not in _SECURITY_PROFILES:
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox security profile is invalid"
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < float(timeout_seconds) <= 3600
        ):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox timeout must be within "
                "(0, 3600] seconds"
            )
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= 1024 * 1024 * 1024
        ):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox output limit must be within "
                "[1, 1073741824] bytes"
            )
        if not callable(command_runner) or not callable(executable_finder):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox runner is invalid"
            )
        self._artifact_root = Path(artifact_root)
        self._engine = engine
        self._image_reference = validate_image_reference(image_reference)
        self._security_profile = security_profile
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._command_runner = command_runner
        self._executable_finder = executable_finder
        selected = os.environ if host_environ is None else host_environ
        raw_path = selected.get("PATH", "")
        self._engine_environ = {
            "PATH": raw_path if isinstance(raw_path, str) else ""
        }
        for name in _ENGINE_ENVIRONMENT_NAMES:
            value = selected.get(name)
            if isinstance(value, str) and value:
                self._engine_environ[name] = value

    def preflight(self) -> None:
        """Verify one approved engine, image, and enforced safety flags."""

        if self._executable_finder(self._engine) is None:
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox engine is unavailable"
            )
        command = [
            self._engine,
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            self._image_reference,
        ]
        try:
            completed = self._command_runner(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=dict(self._engine_environ),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox image preflight failed"
            ) from None
        if getattr(completed, "returncode", None) != 0:
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox image is unavailable"
            )
        try:
            completed = self._command_runner(
                self._capability_probe_command(),
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=dict(self._engine_environ),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox safety preflight failed"
            ) from None
        if getattr(completed, "returncode", None) != 0:
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox safety flags are unavailable"
            )

    def execute(
        self,
        code: str,
        *,
        input_path: str | Path,
        case_id: str,
        attempt_index: int,
    ) -> SandboxExecutionResult:
        """Execute through a fixed OCI argv and retain every attempt."""

        if not isinstance(code, str):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench generated code must be text"
            )
        if not isinstance(case_id, str) or not case_id.strip():
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench case ID must be non-empty text"
            )
        if (
            isinstance(attempt_index, bool)
            or not isinstance(attempt_index, int)
            or not 1 <= attempt_index <= 10_000
        ):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench attempt index must be within [1, 10000]"
            )
        source = Path(input_path).resolve()
        if not source.is_file():
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench input workbook is unavailable"
            )

        artifact_dir = self._new_artifact_dir(
            case_id=case_id,
            attempt_index=attempt_index,
        )
        input_dir = artifact_dir / "input"
        runner_dir = artifact_dir / "runner"
        output_dir = artifact_dir / "output"
        input_dir.mkdir()
        runner_dir.mkdir()
        output_dir.mkdir()
        output_dir.chmod(0o777)
        if self._security_profile == "setpriv-wrapper":
            temporary_dir = output_dir / "tmp"
            temporary_dir.mkdir()
            temporary_dir.chmod(0o777)

        staged_input = input_dir / "input.xlsx"
        shutil.copy2(source, staged_input)
        staged_input.chmod(0o444)
        script_path = runner_dir / "solution.py"
        script_path.write_text(
            _runner_script(code),
            encoding="utf-8",
        )
        script_path.chmod(0o444)

        command = self._command(
            input_path=staged_input,
            script_path=script_path,
            output_dir=output_dir,
        )
        redactions = (
            str(self._artifact_root.resolve()),
            str(source),
            self._image_reference,
        )
        try:
            completed = self._command_runner(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=dict(self._engine_environ),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="timeout",
                error_message="sandbox execution timed out",
            )
        except (OSError, subprocess.SubprocessError):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox execution infrastructure failed"
            ) from None

        returncode = getattr(completed, "returncode", None)
        if not isinstance(returncode, int):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench sandbox runner returned invalid status"
            )
        if returncode != 0:
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="execution_error",
                error_message=_safe_error(
                    getattr(completed, "stdout", ""),
                    getattr(completed, "stderr", ""),
                    redactions=redactions,
                ),
                returncode=returncode,
            )

        output_path = output_dir / "output.xlsx"
        if not output_path.exists():
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="output_missing",
                error_message="sandbox output file was not created",
                returncode=returncode,
            )
        if output_path.is_symlink() or not output_path.is_file():
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="output_invalid",
                error_message="sandbox output is not a regular file",
                returncode=returncode,
            )
        try:
            size = output_path.stat().st_size
        except OSError:
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="output_invalid",
                error_message="sandbox output cannot be inspected",
                returncode=returncode,
            )
        if size > self._max_output_bytes:
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="output_too_large",
                error_message="sandbox output exceeds the configured limit",
                returncode=returncode,
            )
        return SandboxExecutionResult(
            ok=True,
            artifact_dir=artifact_dir,
            output_path=output_path,
            returncode=returncode,
        )

    def _new_artifact_dir(
        self,
        *,
        case_id: str,
        attempt_index: int,
    ) -> Path:
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        safe_case = _SAFE_CASE_RE.sub("-", case_id.strip()).strip(".-")
        if not safe_case:
            safe_case = "case"
        prefix = f"{safe_case[:80]}-attempt-{attempt_index:04d}-"
        return Path(
            tempfile.mkdtemp(
                prefix=prefix,
                dir=self._artifact_root,
            )
        )

    def _command(
        self,
        *,
        input_path: Path,
        script_path: Path,
        output_dir: Path,
    ) -> list[str]:
        return [
            *self._sandbox_prefix(),
            "--mount",
            _mount_spec(input_path, "/input/input.xlsx", readonly=True),
            "--mount",
            _mount_spec(script_path, "/runner/solution.py", readonly=True),
            "--mount",
            _mount_spec(output_dir, "/output", readonly=False),
            self._image_reference,
            *self._python_prefix(),
            "/runner/solution.py",
        ]

    def _capability_probe_command(self) -> list[str]:
        return [
            *self._sandbox_prefix(),
            self._image_reference,
            *self._python_prefix(),
            "-c",
            (
                _SET_PRIV_PROBE
                if self._security_profile == "setpriv-wrapper"
                else "pass"
            ),
        ]

    def _sandbox_prefix(self) -> list[str]:
        prefix = [
            self._engine,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            "64",
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--user",
            "65534:65534",
        ]
        if self._security_profile == "engine-security-opt":
            prefix.extend((
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m",
            ))
        return prefix

    def _python_prefix(self) -> list[str]:
        if self._security_profile == "engine-security-opt":
            return ["python", "-I"]
        return [
            "setpriv",
            "--no-new-privs",
            "env",
            *_SET_PRIV_ENVIRONMENT,
            "python",
            "-I",
        ]


class LocalSubprocessExecutor:
    """Run generated code like the historical pipeline in a local subprocess.

    This mode preserves the paper-run execution semantics and attempt
    artifacts, but it is not a security sandbox. Callers must only enable it
    for trusted benchmark inputs on a disposable or otherwise trusted host.
    Provider credentials are removed from the child environment, but the
    generated process still has the operating-system permissions of the
    current user.
    """

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        timeout_seconds: float,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
        python_executable: str | Path | None = None,
        command_runner: Callable[..., Any] = subprocess.run,
        host_environ: Mapping[str, str] | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < float(timeout_seconds) <= 3600
        ):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local execution timeout must be within "
                "(0, 3600] seconds"
            )
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= 1024 * 1024 * 1024
        ):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local output limit must be within "
                "[1, 1073741824] bytes"
            )
        if not callable(command_runner):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local subprocess runner is invalid"
            )
        selected_python = Path(python_executable or sys.executable)
        if not selected_python.is_absolute():
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local Python must be an absolute path"
            )
        self._artifact_root = Path(artifact_root)
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._python_executable = selected_python
        self._command_runner = command_runner
        selected = os.environ if host_environ is None else host_environ
        self._subprocess_environ = _local_subprocess_environment(selected)

    def preflight(self) -> None:
        """Verify the selected interpreter and required spreadsheet imports."""

        if not self._python_executable.is_file():
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local Python is unavailable"
            )
        try:
            completed = self._command_runner(
                [
                    str(self._python_executable),
                    "-I",
                    "-c",
                    "import openpyxl; import pandas",
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=dict(self._subprocess_environ),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local subprocess preflight failed"
            ) from None
        if getattr(completed, "returncode", None) != 0:
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local requirements are unavailable"
            )

    def execute(
        self,
        code: str,
        *,
        input_path: str | Path,
        case_id: str,
        attempt_index: int,
    ) -> SandboxExecutionResult:
        """Execute one retained attempt in the historical subprocess mode."""

        _validate_attempt(code, case_id=case_id, attempt_index=attempt_index)
        source = Path(input_path).resolve()
        if not source.is_file():
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench input workbook is unavailable"
            )
        artifact_dir = _new_attempt_directory(
            self._artifact_root,
            case_id=case_id,
            attempt_index=attempt_index,
        )
        input_dir = artifact_dir / "input"
        runner_dir = artifact_dir / "runner"
        output_dir = artifact_dir / "output"
        input_dir.mkdir()
        runner_dir.mkdir()
        output_dir.mkdir()
        staged_input = input_dir / "input.xlsx"
        shutil.copy2(source, staged_input)
        staged_input.chmod(0o444)
        output_path = output_dir / "output.xlsx"
        script_path = runner_dir / "solution.py"
        script_path.write_text(
            _local_runner_script(
                code,
                input_path=staged_input,
                output_path=output_path,
            ),
            encoding="utf-8",
        )
        script_path.chmod(0o444)
        redactions = (
            str(self._artifact_root.resolve()),
            str(source),
            str(self._python_executable),
        )
        try:
            completed = self._command_runner(
                [
                    str(self._python_executable),
                    "-I",
                    str(script_path),
                ],
                cwd=artifact_dir,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=dict(self._subprocess_environ),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="timeout",
                error_message="local subprocess execution timed out",
            )
        except (OSError, subprocess.SubprocessError):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local subprocess infrastructure failed"
            ) from None
        returncode = getattr(completed, "returncode", None)
        if not isinstance(returncode, int):
            raise SpreadsheetSandboxRuntimeError(
                "SpreadsheetBench local subprocess returned invalid status"
            )
        if returncode != 0:
            return SandboxExecutionResult(
                ok=False,
                artifact_dir=artifact_dir,
                output_path=None,
                error_type="execution_error",
                error_message=_safe_error(
                    getattr(completed, "stdout", ""),
                    getattr(completed, "stderr", ""),
                    redactions=redactions,
                ),
                returncode=returncode,
            )
        output_error = _validate_output(
            output_path,
            artifact_dir=artifact_dir,
            returncode=returncode,
            max_output_bytes=self._max_output_bytes,
        )
        if output_error is not None:
            return output_error
        return SandboxExecutionResult(
            ok=True,
            artifact_dir=artifact_dir,
            output_path=output_path,
            returncode=returncode,
        )


def resolve_workbooks(
    data_root: str | Path,
    input_path: str | Path,
    golden_path: str | Path,
) -> tuple[Path, Path]:
    """Resolve one input/golden pair strictly below the data root."""

    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench data root is unavailable"
        )
    resolved_input = _resolve_workbook(
        root,
        input_path,
        label="input",
    )
    resolved_golden = _resolve_workbook(
        root,
        golden_path,
        label="golden",
    )
    if resolved_input == resolved_golden:
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench input and golden workbooks must be different"
        )
    return resolved_input, resolved_golden


def validate_image_reference(value: object) -> str:
    """Require a raw image ID or name pinned by an OCI sha256 digest."""

    if (
        not isinstance(value, str)
        or not _IMAGE_REFERENCE_RE.fullmatch(value)
    ):
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench sandbox image must use an immutable "
            "sha256 digest"
        )
    return value


def _resolve_workbook(
    root: Path,
    value: str | Path,
    *,
    label: str,
) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise SpreadsheetSandboxRuntimeError(
            f"SpreadsheetBench {label} workbook is unavailable"
        )
    requested = Path(value).expanduser()
    candidate = (
        requested
        if requested.is_absolute()
        else root / requested
    ).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SpreadsheetSandboxRuntimeError(
            f"SpreadsheetBench {label} workbook resolves outside "
            "the data root"
        ) from None
    if not candidate.is_file():
        raise SpreadsheetSandboxRuntimeError(
            f"SpreadsheetBench {label} workbook does not exist"
        )
    if candidate.suffix.casefold() != ".xlsx":
        raise SpreadsheetSandboxRuntimeError(
            f"SpreadsheetBench {label} workbook must be an xlsx file"
        )
    return candidate


def _runner_script(code: str) -> str:
    cleaned = _PATH_ASSIGNMENT_RE.sub("", code).strip()
    if not cleaned:
        cleaned = "pass"
    body = textwrap.indent(cleaned, "    ")
    return (
        "import sys\n"
        "import traceback\n"
        "INPUT_PATH = '/input/input.xlsx'\n"
        "OUTPUT_PATH = '/output/output.xlsx'\n"
        "try:\n"
        f"{body}\n"
        "except Exception:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(2)\n"
    )


def _local_runner_script(
    code: str,
    *,
    input_path: Path,
    output_path: Path,
) -> str:
    cleaned = _PATH_ASSIGNMENT_RE.sub("", code).strip()
    if not cleaned:
        cleaned = "pass"
    body = textwrap.indent(cleaned, "    ")
    return (
        "import sys\n"
        "import traceback\n"
        f"INPUT_PATH = {str(input_path.resolve())!r}\n"
        f"OUTPUT_PATH = {str(output_path.resolve())!r}\n"
        "try:\n"
        f"{body}\n"
        "except Exception:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(2)\n"
    )


def _local_subprocess_environment(
    environ: Mapping[str, str],
) -> dict[str, str]:
    allowed = {
        name: value
        for name in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
        if isinstance((value := environ.get(name)), str) and value
    }
    allowed.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    })
    return allowed


def _validate_attempt(
    code: object,
    *,
    case_id: object,
    attempt_index: object,
) -> None:
    if not isinstance(code, str):
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench generated code must be text"
        )
    if not isinstance(case_id, str) or not case_id.strip():
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench case ID must be non-empty text"
        )
    if (
        isinstance(attempt_index, bool)
        or not isinstance(attempt_index, int)
        or not 1 <= attempt_index <= 10_000
    ):
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench attempt index must be within [1, 10000]"
        )


def _new_attempt_directory(
    artifact_root: Path,
    *,
    case_id: str,
    attempt_index: int,
) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    safe_case = _SAFE_CASE_RE.sub("-", case_id.strip()).strip(".-")
    if not safe_case:
        safe_case = "case"
    prefix = f"{safe_case[:80]}-attempt-{attempt_index:04d}-"
    return Path(tempfile.mkdtemp(prefix=prefix, dir=artifact_root))


def _validate_output(
    output_path: Path,
    *,
    artifact_dir: Path,
    returncode: int,
    max_output_bytes: int,
) -> SandboxExecutionResult | None:
    if not output_path.exists():
        return SandboxExecutionResult(
            ok=False,
            artifact_dir=artifact_dir,
            output_path=None,
            error_type="output_missing",
            error_message="local subprocess output file was not created",
            returncode=returncode,
        )
    if output_path.is_symlink() or not output_path.is_file():
        return SandboxExecutionResult(
            ok=False,
            artifact_dir=artifact_dir,
            output_path=None,
            error_type="output_invalid",
            error_message="local subprocess output is not a regular file",
            returncode=returncode,
        )
    try:
        size = output_path.stat().st_size
    except OSError:
        return SandboxExecutionResult(
            ok=False,
            artifact_dir=artifact_dir,
            output_path=None,
            error_type="output_invalid",
            error_message="local subprocess output cannot be inspected",
            returncode=returncode,
        )
    if size > max_output_bytes:
        return SandboxExecutionResult(
            ok=False,
            artifact_dir=artifact_dir,
            output_path=None,
            error_type="output_too_large",
            error_message="local subprocess output exceeds the configured limit",
            returncode=returncode,
        )
    return None


def _mount_spec(
    source: Path,
    target: str,
    *,
    readonly: bool,
) -> str:
    resolved = str(source.resolve())
    if any(character in resolved for character in (",", "\n", "\r")):
        raise SpreadsheetSandboxRuntimeError(
            "SpreadsheetBench sandbox artifact path is unsupported"
        )
    options = (
        f"type=bind,source={resolved},target={target}"
        + (",readonly" if readonly else "")
    )
    return options


def _safe_error(
    stdout: object,
    stderr: object,
    *,
    redactions: tuple[str, ...],
) -> str:
    parts = [
        value
        for value in (stdout, stderr)
        if isinstance(value, str) and value.strip()
    ]
    rendered = "\n".join(parts).strip()
    for value in sorted(
        {item for item in redactions if item},
        key=len,
        reverse=True,
    ):
        rendered = rendered.replace(value, "<redacted>")
    rendered = _ABSOLUTE_PATH_RE.sub("<host-path>", rendered)
    if not rendered:
        rendered = "sandbox process exited with a non-zero status"
    return rendered[:_MAX_ERROR_CHARACTERS]


__all__ = [
    "LocalSubprocessExecutor",
    "OCISandboxExecutor",
    "SandboxExecutionResult",
    "SpreadsheetSandboxRuntimeError",
    "resolve_workbooks",
    "validate_image_reference",
]
