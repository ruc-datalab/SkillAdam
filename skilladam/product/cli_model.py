"""Call product workflow models through authenticated platform CLIs."""

from __future__ import annotations

import json
import os
import queue
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from skilladam.product.model import PromptedProductModel
from skilladam.product.models import OptimizationTask


PLATFORM_CLI_NAMES = {
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
    "github-copilot": "copilot",
}
SUPPORTED_PLATFORMS = frozenset(PLATFORM_CLI_NAMES)
DEFAULT_PLATFORM_CLI_MODELS = {
    "claude-code": "sonnet",
    "codex": "gpt-5.6-sol",
}
DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS = 600
REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


class PlatformCliError(RuntimeError):
    """The platform CLI could not provide a usable model response."""


class PlatformCliProductModel(PromptedProductModel):
    """Text model implementation reusing the host platform login."""

    def __init__(
        self,
        *,
        platform: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
        executable: str | None = None,
        timeout_seconds: int = DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS,
    ) -> None:
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"unsupported platform CLI {platform!r}")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, int
        ):
            raise ValueError("timeout_seconds must be an integer")
        if not 10 <= timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be between 10 and 1800")
        self.platform = platform
        self.model = model.strip() if model else None
        if self.model is None:
            self.model = DEFAULT_PLATFORM_CLI_MODELS.get(self.platform)
        self.reasoning_effort = _optional_reasoning_effort(reasoning_effort)
        self.executable = executable.strip() if executable else None
        self.timeout_seconds = timeout_seconds
        self._last_trajectory: tuple[Mapping[str, Any], ...] = ()

    def rollout(self, skill: str, task: OptimizationTask) -> Any:
        result = self._generate(
            "严格按照以下 Skill 完成用户任务。\n\n" + skill,
            task.prompt,
        )
        return {
            "output": result,
            "trajectory": tuple(dict(item) for item in self._last_trajectory),
            "metadata": {"platform": self.platform},
        }

    def _generate(self, system: str, user: str) -> str:
        self._last_trajectory = ()
        executable = _resolve_executable(
            self.executable or PLATFORM_CLI_NAMES[self.platform],
            platform=self.platform,
        )
        with tempfile.TemporaryDirectory(prefix="skilladam-model-") as root:
            workspace = Path(root)
            if self.platform == "claude-code":
                return self._claude(executable, system, user, workspace)
            prompt = _combined_prompt(system, user)
            if self.platform == "codex":
                return self._codex(executable, prompt, workspace)
            if self.platform == "cursor":
                return self._cursor(executable, prompt, workspace)
            return self._copilot(executable, prompt, workspace)

    def _claude(
        self,
        executable: str,
        system: str,
        user: str,
        workspace: Path,
    ) -> str:
        command = [
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--effort",
            self.reasoning_effort or "low",
            "--safe-mode",
            "--tools",
            "",
            "--no-session-persistence",
            "--system-prompt",
            system,
        ]
        if self.model:
            command.extend(["--model", self.model])
        completed = self._run(
            command,
            input_text=user,
            cwd=workspace,
            jsonl_terminal=True,
        )
        terminal = _terminal_json(completed.stdout)
        if terminal.get("is_error") is True:
            raise PlatformCliError(
                "Claude Code returned an error: "
                + _short_detail(terminal.get("result") or completed.stderr)
            )
        result = _required_result(terminal, "Claude Code")
        self._last_trajectory = _ensure_final_message(
            _claude_messages(completed.stdout),
            result,
        )
        return result

    def _codex(
        self,
        executable: str,
        prompt: str,
        workspace: Path,
    ) -> str:
        output = workspace / "last-message.txt"
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--json",
            "-c",
            "features.plugins=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.hooks=false",
            "-c",
            "features.memories=false",
            "-o",
            str(output),
        ]
        if self.model:
            command.extend(["--model", self.model])
        if self.reasoning_effort:
            command.extend(
                ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
            )
        completed = self._run(command, input_text=prompt, cwd=workspace)
        try:
            result = output.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PlatformCliError(
                f"Codex did not create the final response file: {exc}"
            ) from exc
        if not result:
            raise PlatformCliError("Codex returned an empty response")
        self._last_trajectory = _ensure_final_message(
            _codex_messages(completed.stdout),
            result,
        )
        return result

    def _cursor(
        self,
        executable: str,
        prompt: str,
        workspace: Path,
    ) -> str:
        command = [
            executable,
            "-p",
            "--output-format",
            "json",
            "--trust",
            "--workspace",
            str(workspace),
            "--mode",
            "ask",
        ]
        if self.model:
            model = self.model
            if self.reasoning_effort and "[" not in model:
                model = f"{model}[effort={self.reasoning_effort}]"
            command.extend(["--model", model])
        completed = self._run(command, input_text=prompt, cwd=workspace)
        terminal = _terminal_json(completed.stdout)
        if terminal.get("is_error") is True:
            raise PlatformCliError(
                "Cursor Agent returned an error: "
                + _short_detail(
                    terminal.get("result")
                    or terminal.get("error")
                    or completed.stderr
                )
            )
        result = _required_result(terminal, "Cursor Agent")
        self._last_trajectory = _ensure_final_message((), result)
        return result

    def _copilot(
        self,
        executable: str,
        prompt: str,
        workspace: Path,
    ) -> str:
        command = [
            executable,
            "--output-format",
            "json",
            "--stream",
            "off",
            "--no-color",
            "--log-level",
            "none",
            "--no-custom-instructions",
            "--no-ask-user",
            "--allow-all-tools",
            "--allow-all-urls",
            "--disable-mcp-server",
            "skilladam",
        ]
        if self.model:
            command.extend(["--model", self.model])
        if self.reasoning_effort:
            command.extend(["--effort", self.reasoning_effort])
        completed = self._run(
            command,
            input_text=prompt,
            cwd=workspace,
            jsonl_terminal=True,
        )
        parts: list[str] = []
        result_event: Mapping[str, Any] | None = None
        error_event: Mapping[str, Any] | None = None
        for line in completed.stdout.splitlines():
            value = _json_mapping(line)
            if value is None:
                continue
            if value.get("type") == "assistant.message":
                data = value.get("data")
                if isinstance(data, Mapping):
                    content = data.get("content")
                    if isinstance(content, str) and content:
                        parts.append(content)
            elif value.get("type") == "session.error":
                data = value.get("data")
                if isinstance(data, Mapping):
                    error_event = data
            elif value.get("type") == "result":
                result_event = value
        if result_event is None:
            raise PlatformCliError("GitHub Copilot CLI did not return a result event")
        exit_code = result_event.get("exitCode")
        if exit_code not in (None, 0):
            raise PlatformCliError(
                "GitHub Copilot CLI returned an error result: "
                + _short_detail(error_event or result_event)
            )
        result = "\n".join(parts).strip()
        if not result:
            raise PlatformCliError("GitHub Copilot CLI returned an empty response")
        self._last_trajectory = _ensure_final_message(
            _copilot_messages(completed.stdout),
            result,
        )
        return result

    def _run(
        self,
        command: list[str],
        *,
        input_text: str,
        cwd: Path,
        jsonl_terminal: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["NO_COLOR"] = "1"
        if self.platform == "claude-code":
            nested_session = "CLAUDECODE" in environment or any(
                key.startswith("CLAUDE_CODE_") for key in environment
            )
            # Claude Code uses these markers to block nested CLI sessions. They are not
            # credentials; removing them retains OAuth/keychain access without a nested-session error.
            for key in tuple(environment):
                if key == "CLAUDECODE" or key == "CLAUDE_PID" or key.startswith(
                    "CLAUDE_CODE_"
                ):
                    environment.pop(key, None)
            if nested_session:
                # Temporary token/base URL values from the outer Claude session would route inner calls
                # back to that waiting session. Remove them to use local OAuth/keychain credentials.
                environment.pop("ANTHROPIC_AUTH_TOKEN", None)
                environment.pop("ANTHROPIC_BASE_URL", None)
        try:
            runner = _run_jsonl_process if jsonl_terminal else _run_process
            completed = runner(
                command,
                cwd=str(cwd),
                input=input_text,
                timeout=self.timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise PlatformCliError(
                f"{self.platform} CLI timed out after {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise PlatformCliError(
                f"Cannot start {self.platform} CLI: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = _short_detail(completed.stderr)
            raise PlatformCliError(
                f"{self.platform} CLI exit code {completed.returncode}"
                + (f"：{detail}" if detail else "")
            )
        return completed


def _run_process(
    command: list[str],
    *,
    cwd: str,
    input: str,
    timeout: float,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    process = _start_process(command, cwd=cwd, env=env)
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.communicate()
        raise
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )


def _run_jsonl_process(
    command: list[str],
    *,
    cwd: str,
    input: str,
    timeout: float,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Finish on a JSONL result so Windows background handles cannot block EOF."""

    process = _start_process(command, cwd=cwd, env=env)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_queue: queue.Queue[str | None] = queue.Queue()

    def _read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_queue.put(line)
        stdout_queue.put(None)

    def _read_stderr() -> None:
        assert process.stderr is not None
        stderr_lines.extend(process.stderr.readlines())

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    assert process.stdin is not None
    try:
        process.stdin.write(input)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    deadline = time.monotonic() + timeout
    terminal_seen = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            process.wait(timeout=10)
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output="".join(stdout_lines),
                stderr="".join(stderr_lines),
            )
        try:
            line = stdout_queue.get(timeout=remaining)
        except queue.Empty:
            continue
        if line is None:
            break
        stdout_lines.append(line)
        value = _json_mapping(line)
        if value is not None and value.get("type") == "result":
            terminal_seen = True
            break

    if terminal_seen and process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            process.wait(timeout=10)
    elif process.poll() is None:
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            process.wait(timeout=10)
            raise
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    return subprocess.CompletedProcess(
        command,
        0 if terminal_seen else process.returncode,
        "".join(stdout_lines),
        "".join(stderr_lines),
    )


def _start_process(
    command: list[str],
    *,
    cwd: str,
    env: Mapping[str, str],
) -> subprocess.Popen[str]:
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(env),
        shell=False,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _resolve_executable(value: str, *, platform: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    path_like = any(separator in expanded for separator in ("/", "\\"))
    if path_like:
        path = Path(expanded).resolve()
        if path.is_file():
            if platform == "claude-code":
                path = _native_claude_executable(path)
            return str(path)
        found = None
    else:
        found = next(
            (
                candidate
                for name in _executable_names(expanded, platform=platform)
                if (candidate := shutil.which(name))
            ),
            None,
        )
    if found:
        resolved = Path(found).resolve()
        if platform == "claude-code":
            resolved = _native_claude_executable(resolved)
        return str(resolved)
    if platform == "cursor":
        installed = _installed_cursor_agent()
        if installed is not None:
            return str(installed)
        raise PlatformCliError(
            "Cursor Agent CLI (cursor-agent) was not found. The Cursor desktop command cursor "
            "is not the headless Agent CLI; install and sign in to cursor-agent, or explicitly provide "
            "executable."
        )
    raise PlatformCliError(
        f"Could not find {platform} CLI executable {value!r}; "
        "install and sign in to the platform, or explicitly provide executable."
    )


def _executable_names(
    value: str,
    *,
    platform: str,
    windows: bool | None = None,
) -> tuple[str, ...]:
    """Prefer native Windows entry points over extensionless npm shims."""

    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows or Path(value).suffix:
        return (value,)
    if platform == "codex":
        # WindowsApps desktop codex.exe may be discoverable but not executable as a child;
        # the npm .cmd shim can be launched by CreateProcess.
        return (f"{value}.cmd", f"{value}.exe", value)
    return (f"{value}.exe", f"{value}.cmd", f"{value}.bat", value)


def _optional_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in REASONING_EFFORTS:
        raise ValueError(
            "reasoning_effort must be one of "
            + ", ".join(sorted(REASONING_EFFORTS))
        )
    return normalized


def _installed_cursor_agent() -> Path | None:
    """Find CLIs installed on Windows before the current process inherited their PATH entries."""

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    root = Path(local_app_data).expanduser() / "cursor-agent"
    for name in ("cursor-agent.exe", "cursor-agent.cmd"):
        candidate = root / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _native_claude_executable(found: Path) -> Path:
    """Work around Windows npm .cmd forwarding failures with long stdin."""

    if found.suffix.lower() not in {".cmd", ".bat"}:
        return found
    candidate = (
        found.parent
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    return candidate.resolve() if candidate.is_file() else found


def _claude_messages(raw: str) -> tuple[Mapping[str, Any], ...]:
    messages: list[Mapping[str, Any]] = []
    for line in raw.splitlines():
        event = _json_mapping(line)
        if event is None or event.get("type") not in {"assistant", "user"}:
            continue
        message = event.get("message")
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if event.get("type") == "assistant":
            text_parts: list[str] = []
            tool_calls: list[Mapping[str, Any]] = []
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                if block.get("type") == "text" and isinstance(
                    block.get("text"), str
                ):
                    text_parts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": str(block.get("id", "")),
                            "function": {
                                "name": str(block.get("name", "")),
                                "arguments": block.get("input", {}),
                            },
                        }
                    )
            item: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts),
            }
            if tool_calls:
                item["tool_calls"] = tool_calls
            if item["content"] or tool_calls:
                messages.append(item)
        else:
            for block in content:
                if not isinstance(block, Mapping) or block.get("type") != "tool_result":
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(block.get("tool_use_id", "")),
                        "content": _event_text(block.get("content", "")),
                    }
                )
    return tuple(messages)


def _codex_messages(raw: str) -> tuple[Mapping[str, Any], ...]:
    messages: list[Mapping[str, Any]] = []
    for line in raw.splitlines():
        event = _json_mapping(line)
        if event is None or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type == "agent_message" and isinstance(item.get("text"), str):
            messages.append(
                {"role": "assistant", "content": str(item["text"])}
            )
        elif item_type == "command_execution":
            command = _event_text(item.get("command", ""))
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "shell",
                                "arguments": {"command": command},
                            }
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "name": "shell",
                    "content": _event_text(
                        item.get("aggregated_output", "")
                    ),
                }
            )
    return tuple(messages)


def _copilot_messages(raw: str) -> tuple[Mapping[str, Any], ...]:
    messages: list[Mapping[str, Any]] = []
    for line in raw.splitlines():
        event = _json_mapping(line)
        if event is None or event.get("type") != "assistant.message":
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        content = data.get("content")
        if isinstance(content, str) and content:
            messages.append({"role": "assistant", "content": content})
    return tuple(messages)


def _ensure_final_message(
    messages: tuple[Mapping[str, Any], ...],
    result: str,
) -> tuple[Mapping[str, Any], ...]:
    if messages and messages[-1].get("role") == "assistant" and (
        messages[-1].get("content") == result
    ):
        return messages
    return messages + ({"role": "assistant", "content": result},)


def _event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _combined_prompt(system: str, user: str) -> str:
    return (
        "以下 SYSTEM INSTRUCTIONS 优先于 USER REQUEST，必须严格遵循。\n\n"
        "<SYSTEM_INSTRUCTIONS>\n"
        f"{system}\n"
        "</SYSTEM_INSTRUCTIONS>\n\n"
        "<USER_REQUEST>\n"
        f"{user}\n"
        "</USER_REQUEST>"
    )


def _terminal_json(raw: str) -> Mapping[str, Any]:
    candidates = [raw.strip()] if raw.strip() else []
    candidates.extend(
        line.strip()
        for line in raw.splitlines()
        if line.strip().startswith("{")
    )
    for candidate in reversed(candidates):
        value = _json_mapping(candidate)
        if value is not None and value.get("type") == "result":
            return value
    raise PlatformCliError("Platform CLI did not return a recognized JSON result")


def _json_mapping(value: str) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError, TypeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _required_result(value: Mapping[str, Any], platform: str) -> str:
    result = value.get("result")
    if not isinstance(result, str) or not result.strip():
        raise PlatformCliError(f"{platform} returned an empty response")
    return result.strip()


def _short_detail(value: Any) -> str:
    if isinstance(value, str):
        detail = value
    else:
        try:
            detail = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            detail = type(value).__name__
    return " ".join(detail.split())[:500]


__all__ = [
    "PLATFORM_CLI_NAMES",
    "SUPPORTED_PLATFORMS",
    "DEFAULT_PLATFORM_CLI_MODELS",
    "DEFAULT_PLATFORM_CLI_TIMEOUT_SECONDS",
    "PlatformCliError",
    "PlatformCliProductModel",
]
