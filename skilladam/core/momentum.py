"""Persistent optimization memory for SkillAdam iterations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from skilladam.core.type_taxonomy import TaxonomySpec


@dataclass
class AttemptRecord:
    """One attempted change associated with a tracked problem."""

    iteration: int
    approach: str
    problem_addressed: bool
    gate_accepted: bool | None = None


@dataclass
class DisobedienceEvent:
    """Trajectory-level violations observed during one iteration."""

    iteration: int
    violations: list[str] = field(default_factory=list)


@dataclass
class ProblemEntry:
    """One recurring problem in optimization memory."""

    label: str
    description: str
    evidence: str
    status: str = "open"
    resolve_evidence: str = ""
    attempts: list[AttemptRecord] = field(default_factory=list)
    disobedience_events: list[DisobedienceEvent] = field(default_factory=list)
    has_concrete_examples_added: bool = False
    examples_state_evidence: str = ""
    examples_state_iteration: int = -1
    failure_type: str = ""
    failure_type_source: str = ""
    inherited_from_iteration: bool = False
    failure_type_history: list[dict[str, Any]] = field(default_factory=list)


class MomentumTracker:
    """Maintain recurring problems and their accepted or rejected attempts."""

    def __init__(self) -> None:
        self.problems: dict[str, ProblemEntry] = {}
        self._current_iteration = 0
        self._current_attempts: list[AttemptRecord] = []

    def set_iteration(self, iteration: int) -> None:
        if iteration < 0:
            raise ValueError("iteration must be non-negative")
        self._current_iteration = iteration
        self._current_attempts = []

    def execute_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute one dependency-free momentum tool call."""

        handlers = {
            "report_problem": self._report_problem,
            "resolve_problem": self._resolve_problem,
            "reopen_problem": self._reopen_problem,
            "record_attempt": self._record_attempt,
            "report_disobedience": self._report_disobedience,
            "set_examples_state": self._set_examples_state,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"Unknown tool: {name}"
        try:
            return handler(**arguments)
        except (TypeError, ValueError) as exc:
            return f"Tool {name!r} call failed: {exc}"

    def _report_problem(
        self,
        label: str,
        description: str,
        evidence: str,
        *,
        failure_type: str = "",
        inherited_from_iteration: bool | None = None,
    ) -> str:
        normalized = _normalize_label(label)
        if normalized in self.problems:
            entry = self.problems[normalized]
            entry.description = description.strip() or entry.description
            entry.evidence = evidence.strip()
            if entry.status == "resolved":
                entry.status = "open"
                entry.resolve_evidence = ""
            self._update_failure_type(
                entry,
                failure_type=failure_type,
                inherited=inherited_from_iteration,
            )
            return f"Updated existing problem {normalized!r}."

        entry = ProblemEntry(
            label=normalized,
            description=_non_empty(description, "description"),
            evidence=_non_empty(evidence, "evidence"),
        )
        self._update_failure_type(
            entry,
            failure_type=failure_type,
            inherited=inherited_from_iteration,
        )
        self.problems[normalized] = entry
        return f"Added new problem {normalized!r}."

    def _update_failure_type(
        self,
        entry: ProblemEntry,
        *,
        failure_type: str,
        inherited: bool | None,
    ) -> None:
        normalized = failure_type.strip()
        if not normalized:
            return
        source = "iteration" if inherited else "momentum_self"
        if (
            normalized == entry.failure_type
            and source == entry.failure_type_source
        ):
            return
        entry.failure_type = normalized
        entry.failure_type_source = source
        entry.inherited_from_iteration = bool(inherited)
        entry.failure_type_history.append(
            {
                "iteration": self._current_iteration,
                "type": normalized,
                "source": source,
            }
        )

    def _resolve_problem(self, label: str, evidence: str) -> str:
        entry = self._find(label)
        if entry is None:
            return f"Problem {_normalize_label(label)!r} not found in tracker."
        if entry.status == "resolved":
            return f"Problem {entry.label!r} is already resolved."
        normalized_evidence = _non_empty(evidence, "evidence")
        entry.status = "resolved"
        entry.resolve_evidence = normalized_evidence
        return f"Marked {entry.label!r} as resolved."

    def _reopen_problem(self, label: str, evidence: str) -> str:
        entry = self._find(label)
        if entry is None:
            return f"Problem {_normalize_label(label)!r} not found in tracker."
        if entry.status != "resolved":
            return f"Problem {entry.label!r} is not resolved."
        normalized_evidence = _non_empty(evidence, "evidence")
        entry.status = "open"
        entry.evidence = normalized_evidence
        entry.resolve_evidence = ""
        return f"Reopened {entry.label!r}."

    def _record_attempt(
        self,
        label: str,
        approach: str,
        problem_addressed: bool | str,
    ) -> str:
        entry = self._find(label)
        if entry is None:
            return f"Problem {_normalize_label(label)!r} not found in tracker."
        addressed = _as_bool(problem_addressed)
        record = AttemptRecord(
            iteration=self._current_iteration,
            approach=_non_empty(approach, "approach"),
            problem_addressed=addressed,
        )
        entry.attempts.append(record)
        self._current_attempts.append(record)
        return f"Recorded attempt for {entry.label!r}."

    def _report_disobedience(
        self,
        label: str,
        violations: list[str] | str,
    ) -> str:
        entry = self._find(label)
        if entry is None:
            return f"Problem {_normalize_label(label)!r} not found in tracker."
        values = [violations] if isinstance(violations, str) else violations
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            return f"No violations provided for {entry.label!r}; skipped."
        existing = next(
            (
                event
                for event in entry.disobedience_events
                if event.iteration == self._current_iteration
            ),
            None,
        )
        if existing is None:
            entry.disobedience_events.append(
                DisobedienceEvent(
                    iteration=self._current_iteration,
                    violations=cleaned,
                )
            )
        else:
            existing.violations = cleaned
        return f"Recorded {len(cleaned)} violations for {entry.label!r}."

    def _set_examples_state(
        self,
        label: str,
        examples_present: bool | str,
        evidence: str = "",
    ) -> str:
        entry = self._find(label)
        if entry is None:
            return f"Problem {_normalize_label(label)!r} not found in tracker."
        entry.has_concrete_examples_added = _as_bool(examples_present)
        entry.examples_state_evidence = evidence.strip()
        entry.examples_state_iteration = self._current_iteration
        return f"Updated examples state for {entry.label!r}."

    def _find(self, label: str) -> ProblemEntry | None:
        return self.problems.get(_normalize_label(label))

    def fill_gate_result(self, accepted: bool) -> None:
        """Attach the gate result to attempts recorded this iteration."""

        for attempt in self._current_attempts:
            attempt.gate_accepted = bool(accepted)

    def render_memory(self) -> str:
        """Render concise memory for the iteration agent."""

        resolved = [
            item for item in self.problems.values() if item.status == "resolved"
        ]
        open_items = [
            item for item in self.problems.values() if item.status == "open"
        ]
        lines = ["## Optimization Memory", "", "### Resolved"]
        lines.extend(
            [f"- **{item.label}**: {item.description}" for item in resolved]
            or ["(none)"]
        )
        lines.extend(["", "### Open"])
        if not open_items:
            lines.append("(none)")
        for item in open_items:
            lines.append(f"- **{item.label}**: {item.description}")
            for attempt in item.attempts:
                gate = (
                    "accepted"
                    if attempt.gate_accepted is True
                    else "rejected"
                    if attempt.gate_accepted is False
                    else "pending"
                )
                lines.append(
                    f"  - iter_{attempt.iteration}: {attempt.approach} "
                    f"[addressed={_yes_no(attempt.problem_addressed)}, "
                    f"gate={gate}]"
                )
        return "\n".join(lines)

    def render_for_agent_input(self) -> str:
        """Render structured state for the momentum update agent."""

        if not self.problems:
            return "No problems tracked yet."
        lines: list[str] = []
        for entry in self.problems.values():
            lines.append(
                f"[{entry.status.upper()}] {entry.label}: {entry.description}"
            )
            for attempt in entry.attempts:
                gate = (
                    "accepted"
                    if attempt.gate_accepted is True
                    else "rejected"
                    if attempt.gate_accepted is False
                    else "pending"
                )
                lines.append(
                    f"  iter_{attempt.iteration}: {attempt.approach} "
                    f"[addressed={_yes_no(attempt.problem_addressed)}, "
                    f"gate={gate}]"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "current_iteration": self._current_iteration,
            "problems": {
                key: asdict(value) for key, value in self.problems.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MomentumTracker":
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported momentum schema_version")
        tracker = cls()
        tracker._current_iteration = int(payload.get("current_iteration", 0))
        for key, raw_value in payload.get("problems", {}).items():
            value = dict(raw_value)
            attempts = [
                AttemptRecord(**item) for item in value.pop("attempts", [])
            ]
            events = [
                DisobedienceEvent(**item)
                for item in value.pop("disobedience_events", [])
            ]
            tracker.problems[key] = ProblemEntry(
                **value,
                attempts=attempts,
                disobedience_events=events,
            )
        return tracker

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "MomentumTracker":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def build_momentum_tools(
    taxonomy: TaxonomySpec | None = None,
) -> list[dict[str, Any]]:
    """Return JSON schemas with exact runtime argument types."""

    report_properties: dict[str, Any] = {
        "label": {"type": "string"},
        "description": {"type": "string"},
        "evidence": {"type": "string"},
    }
    report_required = ["label", "description", "evidence"]
    if taxonomy is not None:
        report_properties["failure_type"] = {
            "type": "string",
            "enum": taxonomy.failure_type_names,
        }
        report_properties["inherited_from_iteration"] = {
            "type": "boolean"
        }
        report_required.extend(
            ["failure_type", "inherited_from_iteration"]
        )

    return [
        _tool_schema(
            "report_problem",
            properties=report_properties,
            required=report_required,
        ),
        _tool_schema(
            "resolve_problem",
            properties={
                "label": {"type": "string"},
                "evidence": {"type": "string"},
            },
            required=["label", "evidence"],
        ),
        _tool_schema(
            "reopen_problem",
            properties={
                "label": {"type": "string"},
                "evidence": {"type": "string"},
            },
            required=["label", "evidence"],
        ),
        _tool_schema(
            "record_attempt",
            properties={
                "label": {"type": "string"},
                "approach": {"type": "string"},
                "problem_addressed": {"type": "boolean"},
            },
            required=["label", "approach", "problem_addressed"],
        ),
        _tool_schema(
            "report_disobedience",
            properties={
                "label": {"type": "string"},
                "violations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            required=["label", "violations"],
        ),
        _tool_schema(
            "set_examples_state",
            properties={
                "label": {"type": "string"},
                "examples_present": {"type": "boolean"},
                "evidence": {"type": "string"},
            },
            required=["label", "examples_present"],
        ),
    ]


def _tool_schema(
    name: str,
    *,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _normalize_label(label: str) -> str:
    normalized = str(label or "").strip().lower()
    if not normalized:
        raise ValueError("label must be non-empty")
    return normalized


def _non_empty(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _as_bool(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"true", "yes", "1"}


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
