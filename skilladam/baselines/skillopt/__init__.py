"""Minimal independent SkillOpt reproduction."""

from skilladam.baselines.skillopt.contracts import (
    EpochRecord,
    OptimizerCall,
    OptimizerResult,
    RolloutBatch,
    RolloutCall,
    SkillOptCallbacks,
    SkillOptConfig,
    SkillOptRunResult,
    SkillOptState,
    SkillVersion,
)
from skilladam.baselines.skillopt.gate import (
    GateConfig,
    GateTransition,
    evaluate_gate,
    select_gate_score,
)
from skilladam.baselines.skillopt.runner import (
    CheckpointExistsError,
    ResumeMismatchError,
    SkillOptRunner,
)
from skilladam.baselines.skillopt.scheduler import (
    AUTONOMOUS_BUDGET,
    EditBudgetScheduler,
    build_scheduler,
)
from skilladam.baselines.skillopt.slow_update import (
    LongitudinalPair,
    SLOW_UPDATE_END,
    SLOW_UPDATE_START,
    apply_force_slow_update,
    apply_gated_slow_update,
    build_longitudinal_pairs,
    extract_slow_update_block,
    inject_slow_update_block,
    replace_slow_update_block,
)

__all__ = [
    "AUTONOMOUS_BUDGET",
    "CheckpointExistsError",
    "EditBudgetScheduler",
    "EpochRecord",
    "GateConfig",
    "GateTransition",
    "LongitudinalPair",
    "OptimizerCall",
    "OptimizerResult",
    "ResumeMismatchError",
    "RolloutBatch",
    "RolloutCall",
    "SLOW_UPDATE_END",
    "SLOW_UPDATE_START",
    "SkillOptCallbacks",
    "SkillOptConfig",
    "SkillOptRunResult",
    "SkillOptRunner",
    "SkillOptState",
    "SkillVersion",
    "apply_force_slow_update",
    "apply_gated_slow_update",
    "build_longitudinal_pairs",
    "build_scheduler",
    "evaluate_gate",
    "extract_slow_update_block",
    "inject_slow_update_block",
    "replace_slow_update_block",
    "select_gate_score",
]
