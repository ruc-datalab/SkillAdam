"""Safe runtime boundary for the optional upstream ALFWorld environment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import math
import multiprocessing
import os
from pathlib import Path, PurePosixPath
from typing import Any


class ALFWorldRuntimeError(RuntimeError):
    """One path-free ALFWorld environment failure."""


@dataclass(frozen=True, slots=True)
class ALFWorldState:
    """One normalized state returned by the optional environment worker."""

    observation: str
    admissible_actions: tuple[str, ...]
    reward: float
    done: bool
    won: bool


@dataclass(frozen=True, slots=True)
class _EnvironmentSpec:
    gamefile: str
    environment_split: str
    seed: int
    max_steps: int


class ALFWorldEnvironmentSession:
    """Bounded parent-side RPC session for one ALFWorld game."""

    def __init__(
        self,
        *,
        process: Any,
        connection: Any,
        step_timeout_seconds: float,
    ) -> None:
        self._process = process
        self._connection = connection
        self._step_timeout_seconds = step_timeout_seconds
        self._closed = False

    @classmethod
    def start(
        cls,
        spec: _EnvironmentSpec,
        *,
        startup_timeout_seconds: float,
        step_timeout_seconds: float,
        context_factory: Callable[[], Any] | None = None,
    ) -> ALFWorldEnvironmentSession:
        """Start one spawn-safe environment worker and await readiness."""

        context = (
            multiprocessing.get_context("spawn")
            if context_factory is None
            else context_factory()
        )
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_environment_worker,
            args=(child, spec),
            daemon=False,
        )
        process.start()
        child.close()
        session = cls(
            process=process,
            connection=parent,
            step_timeout_seconds=step_timeout_seconds,
        )
        try:
            session._receive(
                stage="startup",
                timeout_seconds=startup_timeout_seconds,
            )
        except BaseException:
            session._force_stop()
            raise
        return session

    def reset(self) -> ALFWorldState:
        """Reset the exact game selected by the runtime spec."""

        return self._state_request("reset", None)

    def step(self, action: str) -> ALFWorldState:
        """Execute one non-empty text action."""

        if not isinstance(action, str) or not action.strip():
            raise ALFWorldRuntimeError(
                "ALFWorld environment action must be non-empty text"
            )
        return self._state_request("step", action.strip())

    def close(self) -> None:
        """Close idempotently, terminating a worker that does not exit."""

        if self._closed:
            return
        try:
            if self._process.is_alive():
                try:
                    self._connection.send(("close", None))
                    self._receive(
                        stage="close",
                        timeout_seconds=self._step_timeout_seconds,
                    )
                except (ALFWorldRuntimeError, OSError, EOFError):
                    pass
                self._process.join(
                    timeout=self._step_timeout_seconds
                )
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=1.0)
                if self._process.is_alive():
                    self._process.kill()
                    self._process.join(timeout=1.0)
        finally:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> ALFWorldEnvironmentSession:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()

    def _state_request(
        self,
        command: str,
        payload: object,
    ) -> ALFWorldState:
        if self._closed or not self._process.is_alive():
            raise ALFWorldRuntimeError(
                "ALFWorld environment worker is not running"
            )
        try:
            self._connection.send((command, payload))
        except (OSError, EOFError):
            raise ALFWorldRuntimeError(
                f"ALFWorld environment {command} failed: worker_unavailable"
            ) from None
        value = self._receive(
            stage=command,
            timeout_seconds=self._step_timeout_seconds,
        )
        return _state_from_payload(value, stage=command)

    def _receive(
        self,
        *,
        stage: str,
        timeout_seconds: float,
    ) -> object:
        try:
            ready = self._connection.poll(timeout_seconds)
        except (OSError, EOFError):
            ready = False
        if not ready:
            raise ALFWorldRuntimeError(
                f"ALFWorld environment {stage} timed out"
            )
        try:
            message = self._connection.recv()
        except (OSError, EOFError):
            raise ALFWorldRuntimeError(
                f"ALFWorld environment {stage} failed: worker_unavailable"
            ) from None
        if (
            not isinstance(message, Sequence)
            or isinstance(message, (str, bytes, bytearray))
            or len(message) != 2
        ):
            raise ALFWorldRuntimeError(
                f"ALFWorld environment {stage} failed: invalid_protocol"
            )
        status, payload = message
        if status == "ok":
            return payload
        if status == "error" and isinstance(payload, Mapping):
            error_stage = payload.get("stage")
            error_type = payload.get("error_type")
            if (
                isinstance(error_stage, str)
                and error_stage
                and isinstance(error_type, str)
                and error_type.replace("_", "").isalnum()
            ):
                raise ALFWorldRuntimeError(
                    "ALFWorld environment "
                    f"{error_stage} failed: {error_type}"
                )
        raise ALFWorldRuntimeError(
            f"ALFWorld environment {stage} failed: invalid_protocol"
        )

    def _force_stop(self) -> None:
        try:
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1.0)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=1.0)
        finally:
            self._connection.close()
            self._closed = True


def resolve_gamefile(
    gamefile: object,
    *,
    environ: Mapping[str, str] | None = None,
    data_env: str = "ALFWORLD_DATA",
) -> Path:
    """Resolve one relative gamefile below a caller-managed data root."""

    if not isinstance(gamefile, str) or not gamefile.strip():
        raise ALFWorldRuntimeError(
            "ALFWorld gamefile must be a relative POSIX path"
        )
    if "\\" in gamefile:
        raise ALFWorldRuntimeError(
            "ALFWorld gamefile must be a relative POSIX path"
        )
    relative = PurePosixPath(gamefile)
    if relative.is_absolute() or ".." in relative.parts:
        raise ALFWorldRuntimeError(
            "ALFWorld gamefile must be a relative POSIX path"
        )

    selected = os.environ if environ is None else environ
    raw_root = selected.get(data_env)
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ALFWorldRuntimeError(
            "ALFWorld data-root environment variable is missing or blank"
        )
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise ALFWorldRuntimeError(
            "ALFWorld data root does not exist or is not a directory"
        )
    candidate = root.joinpath(*relative.parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ALFWorldRuntimeError(
            "ALFWorld gamefile resolves outside the data root"
        ) from None
    if not candidate.is_file():
        raise ALFWorldRuntimeError(
            "ALFWorld gamefile does not exist or is not a regular file"
        )
    return candidate


def build_environment_config(
    gamefile: Path,
    *,
    max_steps: int,
) -> dict[str, Any]:
    """Build only the fields used by upstream ``AlfredTWEnv``."""

    if (
        isinstance(max_steps, bool)
        or not isinstance(max_steps, int)
        or not 1 <= max_steps <= 50
    ):
        raise ALFWorldRuntimeError(
            "ALFWorld max_steps must be an integer within [1, 50]"
        )
    parent = str(Path(gamefile).parent)
    return {
        "env": {
            "type": "AlfredTWEnv",
            "goal_desc_human_anns_prob": 0.0,
            "task_types": [1, 2, 3, 4, 5, 6],
            "domain_randomization": False,
            "expert_type": "handcoded",
        },
        "general": {"training_method": "dagger"},
        "dagger": {
            "training": {
                "max_nb_steps_per_episode": max_steps,
            }
        },
        "dataset": {
            "data_path": parent,
            "eval_id_data_path": parent,
            "eval_ood_data_path": parent,
            "num_train_games": 0,
            "num_eval_games": 0,
        },
    }


def open_environment(
    gamefile: Path,
    *,
    environment_split: str,
    seed: int,
    max_steps: int,
    startup_timeout_seconds: float,
    step_timeout_seconds: float,
    context_factory: Callable[[], Any] | None = None,
) -> ALFWorldEnvironmentSession:
    """Open one optional upstream environment in an isolated process."""

    if environment_split not in {
        "train",
        "eval_in_distribution",
        "eval_out_of_distribution",
    }:
        raise ALFWorldRuntimeError(
            "ALFWorld environment split is unsupported"
        )
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ALFWorldRuntimeError(
            "ALFWorld seed must be a non-negative integer"
        )
    build_environment_config(gamefile, max_steps=max_steps)
    startup_timeout = _positive_timeout(
        startup_timeout_seconds,
        "startup",
    )
    step_timeout = _positive_timeout(
        step_timeout_seconds,
        "step",
    )
    return ALFWorldEnvironmentSession.start(
        _EnvironmentSpec(
            gamefile=str(Path(gamefile)),
            environment_split=environment_split,
            seed=seed,
            max_steps=max_steps,
        ),
        startup_timeout_seconds=startup_timeout,
        step_timeout_seconds=step_timeout,
        context_factory=context_factory,
    )


def _environment_worker(
    connection: Any,
    spec: _EnvironmentSpec,
) -> None:
    environment: Any | None = None
    try:
        environment = _create_upstream_environment(spec)
        connection.send(("ok", None))
        while True:
            command, payload = connection.recv()
            if command == "close":
                connection.send(("ok", None))
                return
            try:
                if command == "reset":
                    value = _reset_environment(environment)
                elif command == "step":
                    value = _step_environment(environment, payload)
                else:
                    raise ValueError("unsupported worker command")
            except BaseException as exc:
                connection.send(
                    (
                        "error",
                        {
                            "stage": str(command),
                            "error_type": type(exc).__name__,
                        },
                    )
                )
                return
            connection.send(("ok", value))
    except BaseException as exc:
        try:
            connection.send(
                (
                    "error",
                    {
                        "stage": "startup",
                        "error_type": type(exc).__name__,
                    },
                )
            )
        except BaseException:
            pass
    finally:
        if environment is not None:
            close = getattr(environment, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    pass
        connection.close()


def _create_upstream_environment(spec: _EnvironmentSpec) -> Any:
    from alfworld.agents.environment import get_environment

    gamefile = Path(spec.gamefile)
    config = build_environment_config(
        gamefile,
        max_steps=spec.max_steps,
    )
    base_environment = get_environment("AlfredTWEnv")(
        config,
        train_eval=spec.environment_split,
    )
    base_environment.game_files = [str(gamefile)]
    if hasattr(base_environment, "num_games"):
        base_environment.num_games = 1
    environment = base_environment.init_env(batch_size=1)
    seed_method = getattr(environment, "seed", None)
    if callable(seed_method):
        seed_method(spec.seed)
    return environment


def _reset_environment(environment: Any) -> dict[str, object]:
    value = environment.reset()
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
    ):
        raise ValueError("invalid reset result")
    observations, infos = value
    return _normalize_upstream_state(
        observations=observations,
        reward=0.0,
        done=False,
        infos=infos,
    )


def _step_environment(
    environment: Any,
    action: object,
) -> dict[str, object]:
    if not isinstance(action, str) or not action.strip():
        raise ValueError("invalid action")
    value = environment.step([action.strip()])
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
    ):
        raise ValueError("invalid step result")
    observations, rewards, dones, infos = value
    return _normalize_upstream_state(
        observations=observations,
        reward=_first(rewards, "reward"),
        done=_first(dones, "done"),
        infos=infos,
    )


def _normalize_upstream_state(
    *,
    observations: object,
    reward: object,
    done: object,
    infos: object,
) -> dict[str, object]:
    if not isinstance(infos, Mapping):
        raise ValueError("invalid environment infos")
    observation = _first(observations, "observation")
    if not isinstance(observation, str) or not observation.strip():
        raise ValueError("invalid environment observation")
    actions = _first(
        infos.get("admissible_commands"),
        "admissible_commands",
    )
    if (
        isinstance(actions, (str, bytes, bytearray))
        or not isinstance(actions, Sequence)
        or not actions
        or any(not isinstance(item, str) or not item.strip() for item in actions)
    ):
        raise ValueError("invalid environment admissible_commands")
    won = _first(infos.get("won"), "won")
    reward_number = float(reward)
    if (
        isinstance(reward, bool)
        or not math.isfinite(reward_number)
    ):
        raise ValueError("invalid environment reward")
    return {
        "observation": observation.strip(),
        "admissible_actions": tuple(item.strip() for item in actions),
        "reward": reward_number,
        "done": bool(done),
        "won": bool(won),
    }


def _first(value: object, field_name: str) -> object:
    if isinstance(value, (str, bytes, bytearray)) or value is None:
        raise ValueError(f"invalid environment {field_name}")
    try:
        return value[0]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        raise ValueError(f"invalid environment {field_name}") from None


def _state_from_payload(
    value: object,
    *,
    stage: str,
) -> ALFWorldState:
    if not isinstance(value, Mapping):
        raise ALFWorldRuntimeError(
            f"ALFWorld environment {stage} failed: invalid_state"
        )
    observation = value.get("observation")
    actions = value.get("admissible_actions")
    reward = value.get("reward")
    done = value.get("done")
    won = value.get("won")
    if (
        not isinstance(observation, str)
        or not observation.strip()
        or isinstance(actions, (str, bytes, bytearray))
        or not isinstance(actions, Sequence)
        or not actions
        or any(not isinstance(item, str) or not item.strip() for item in actions)
        or isinstance(reward, bool)
        or not isinstance(reward, (int, float))
        or not math.isfinite(float(reward))
        or not isinstance(done, bool)
        or not isinstance(won, bool)
        or (won and not done)
    ):
        raise ALFWorldRuntimeError(
            f"ALFWorld environment {stage} failed: invalid_state"
        )
    return ALFWorldState(
        observation=observation.strip(),
        admissible_actions=tuple(item.strip() for item in actions),
        reward=float(reward),
        done=done,
        won=won,
    )


def _positive_timeout(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 < float(value) <= 3600.0
    ):
        raise ALFWorldRuntimeError(
            f"ALFWorld {label} timeout must be within (0, 3600]"
        )
    return float(value)


__all__ = [
    "ALFWorldEnvironmentSession",
    "ALFWorldRuntimeError",
    "ALFWorldState",
    "build_environment_config",
    "open_environment",
    "resolve_gamefile",
]
