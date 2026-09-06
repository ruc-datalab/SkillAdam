"""Derive a low-cost availability smoke from frozen paper settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from skilladam.experiments.planning import (
    PlannedCommand,
    build_main_result_commands,
)


_PUBLIC_SKILL_ROOT = Path(__file__).resolve().parents[1] / "artifacts/skills"


def build_api_training_smoke_commands(
    *,
    benchmark: str,
    exact_case_id: str,
    data_root: Path,
    backend: str,
    backend_config: Path,
    output_root: Path,
    skillopt_validation_case_id: str,
    scope: str | None = None,
) -> tuple[PlannedCommand, ...]:
    """Build minimal Stage0/train paths plus one-case evaluations."""

    formal = build_main_result_commands(
        benchmark=benchmark,
        method="all",
        phase="all",
        data_root=data_root,
        backend=backend,
        backend_config=backend_config,
        output_root=output_root,
        scope=scope,
    )
    selected = [
        command
        for command in formal
        if command.scope == scope
    ]
    formal_stage0 = _one(
        selected,
        phase="stage0",
        method="skilladam",
    )
    stage0_case_id = _one_repeatable_value(
        formal_stage0,
        "--stage0-case-id",
    )
    stage0 = _replace_repeatable_flag(
        _replace_flags(
            formal_stage0,
            {
                "--workers": "1",
                "--train-size": "1",
                "--validation-size": "1",
                "--stage0-size": "1",
            },
            label_suffix="one-case-availability-smoke",
        ),
        "--stage0-case-id",
        (stage0_case_id,),
    )
    skilladam_train = _replace_flags(
        _one(selected, phase="train", method="skilladam"),
        {
            "--workers": "1",
            "--iterations": "1",
            "--min-iterations": "1",
            "--train-size": "1",
            "--validation-size": "1",
        },
        label_suffix="one-iteration-one-case-availability-smoke",
    )
    if (
        not isinstance(skillopt_validation_case_id, str)
        or not skillopt_validation_case_id.strip()
    ):
        raise ValueError(
            "SkillOpt smoke validation case ID must be non-empty text"
        )
    skillopt_train = _append_flags(
        _replace_flags(
            _one(selected, phase="train", method="skillopt"),
            {
                "--workers": "1",
                "--batch-size": "1",
                "--reflection-minibatch-size": "1",
                "--analyst-workers": "1",
            },
            label_suffix="one-train-one-validation-availability-smoke",
        ),
        (
            ("--max-batches", "1"),
            ("--smoke-train-case-id", stage0_case_id),
            (
                "--smoke-validation-case-id",
                skillopt_validation_case_id.strip(),
            ),
        ),
    )
    baseline = _exact_eval(
        _one(selected, phase="evaluate", method="baseline"),
        exact_case_id,
        label_suffix="exact-case",
    )
    trained_skilladam = _exact_eval(
        _one(selected, phase="evaluate", method="skilladam"),
        exact_case_id,
        label_suffix="trained-exact-case",
    )
    trained_skillopt = _exact_eval(
        _one(selected, phase="evaluate", method="skillopt"),
        exact_case_id,
        label_suffix="trained-exact-case",
    )
    public_skilladam = _public_skill_eval(
        trained_skilladam,
        benchmark=benchmark,
        scope=scope,
    )
    return (
        stage0,
        skilladam_train,
        skillopt_train,
        baseline,
        trained_skilladam,
        trained_skillopt,
        public_skilladam,
    )


def _one(
    commands: list[PlannedCommand],
    *,
    phase: str,
    method: str,
) -> PlannedCommand:
    matches = [
        command
        for command in commands
        if command.phase == phase and command.method == method
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {method}/{phase} command, found {len(matches)}"
        )
    return matches[0]


def _replace_flags(
    command: PlannedCommand,
    replacements: dict[str, str],
    *,
    label_suffix: str,
) -> PlannedCommand:
    argv = list(command.argv)
    for flag, value in replacements.items():
        try:
            index = argv.index(flag)
        except ValueError:
            raise ValueError(f"planned command is missing {flag}") from None
        argv[index + 1] = value
    return replace(
        command,
        label=f"{command.label}:{label_suffix}",
        argv=tuple(argv),
    )


def _append_flags(
    command: PlannedCommand,
    values: tuple[tuple[str, str], ...],
) -> PlannedCommand:
    argv = list(command.argv)
    for flag, value in values:
        if flag in argv:
            raise ValueError(f"planned command already contains {flag}")
        argv.extend((flag, value))
    return replace(command, argv=tuple(argv))


def _one_repeatable_value(
    command: PlannedCommand,
    flag: str,
) -> str:
    values = tuple(
        command.argv[index + 1]
        for index, value in enumerate(command.argv[:-1])
        if value == flag
    )
    if not values:
        raise ValueError(f"planned command is missing {flag}")
    return values[0]


def _replace_repeatable_flag(
    command: PlannedCommand,
    flag: str,
    values: tuple[str, ...],
) -> PlannedCommand:
    if not values:
        raise ValueError(f"replacement values for {flag} must not be empty")
    argv: list[str] = []
    index = 0
    found = False
    while index < len(command.argv):
        value = command.argv[index]
        if value == flag:
            if index + 1 >= len(command.argv):
                raise ValueError(f"planned command has an incomplete {flag}")
            found = True
            index += 2
            continue
        argv.append(value)
        index += 1
    if not found:
        raise ValueError(f"planned command is missing {flag}")
    for value in values:
        argv.extend((flag, value))
    return replace(command, argv=tuple(argv))


def _exact_eval(
    command: PlannedCommand,
    case_id: str,
    *,
    label_suffix: str,
) -> PlannedCommand:
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("exact smoke case ID must be non-empty text")
    exact = _replace_flags(
        command,
        {"--workers": "1"},
        label_suffix=label_suffix,
    )
    if "--case-id" in exact.argv or "--limit" in exact.argv:
        raise ValueError("formal evaluation already restricts its case set")
    return replace(
        exact,
        argv=(*exact.argv, "--case-id", case_id),
    )


def _public_skill_eval(
    command: PlannedCommand,
    *,
    benchmark: str,
    scope: str | None,
) -> PlannedCommand:
    argv = list(command.argv)
    skill = (
        _PUBLIC_SKILL_ROOT / "deepplanning" / f"{scope}.md"
        if benchmark == "deepplanning"
        else _PUBLIC_SKILL_ROOT / benchmark / "best_skill.md"
    )
    if not skill.is_file():
        raise ValueError("public selected skill artifact is unavailable")
    skill_index = argv.index("--skill") + 1
    argv[skill_index] = str(skill)
    output_index = argv.index("--output-dir") + 1
    current_output = Path(argv[output_index])
    argv[output_index] = str(
        current_output.parent / "public_skilladam_test"
    )
    return replace(
        command,
        label=command.label.replace(
            ":trained-exact-case",
            ":public-selected-skill-exact-case",
        ),
        argv=tuple(argv),
    )


__all__ = ["build_api_training_smoke_commands"]
