"""Model protocols, fixtures, and OpenAI-compatible implementation for product workflows."""

# public-audit: allow=secret-assignment

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from skilladam.core.edit_budget import build_budget_section
from skilladam.core.feedback_loop import IterationContext
from skilladam.core.momentum import build_momentum_tools
from skilladam.product.evaluation_planning import EvaluationPlanner
from skilladam.product.feedback import IterationFeedback, MomentumUpdateContext
from skilladam.product.models import (
    EvaluationSpec,
    OptimizationTask,
    SkillPackageSnapshot,
    TaskDraft,
    UsageIntent,
    canonical_json,
)
from skilladam.product.tasking import (
    SyntheticGenerationRequest,
    ingest_task_records,
)


_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


class ProductModel(Protocol):
    def generate_tasks(
        self,
        request: SyntheticGenerationRequest,
    ) -> Sequence[Mapping[str, Any]]: ...

    def plan_rubric(self, task: TaskDraft) -> Mapping[str, Any]: ...

    def rollout(self, skill: str, task: OptimizationTask) -> Any: ...

    def judge(
        self,
        task: OptimizationTask,
        output: Any,
        spec: EvaluationSpec,
    ) -> Mapping[str, Any]: ...

    def propose_patch(
        self,
        context: IterationContext,
        feedback: IterationFeedback,
        intent: UsageIntent,
        momentum_memory: str,
    ) -> str: ...

    def update_momentum(
        self,
        context: MomentumUpdateContext,
        current_memory: str,
    ) -> Sequence[Mapping[str, Any]]: ...


class FixtureProductModel:
    """Model for repeatable end-to-end and adapter contract tests."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if payload.get("schema_version") != 1:
            raise ValueError("fixture product model requires schema_version 1")
        self.payload = dict(payload)

    @classmethod
    def from_path(cls, path: Path) -> "FixtureProductModel":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("fixture product model must contain an object")
        return cls(payload)

    def generate_tasks(
        self,
        request: SyntheticGenerationRequest,
    ) -> Sequence[Mapping[str, Any]]:
        tasks = self.payload.get("tasks", ())
        if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
            raise ValueError("fixture tasks must be an array")
        return _task_mappings(tasks, "fixture task")

    def plan_rubric(self, task: TaskDraft) -> Mapping[str, Any]:
        return dict(
            self.payload.get(
                "rubric",
                {
                    "dimensions": [
                        {"name": "task_completion", "weight": 1.0}
                    ]
                },
            )
        )

    def rollout(self, skill: str, task: OptimizationTask) -> Any:
        for rule in self.payload.get("rollout_rules", ()):
            marker = str(rule.get("skill_contains", ""))
            if marker and marker in skill:
                outputs = rule.get("outputs", {})
                if task.task_id in outputs:
                    return outputs[task.task_id]
                if "*" in outputs:
                    return outputs["*"]
        defaults = self.payload.get("default_outputs", {})
        if task.task_id in defaults:
            return defaults[task.task_id]
        return defaults.get("*", "")

    def judge(
        self,
        task: OptimizationTask,
        output: Any,
        spec: EvaluationSpec,
    ) -> Mapping[str, Any]:
        configured = self.payload.get("judge", {"score": 0.0})
        if isinstance(configured, Mapping) and task.task_id in configured:
            return dict(configured[task.task_id])
        return dict(configured)

    def propose_patch(
        self,
        context: IterationContext,
        feedback: IterationFeedback,
        intent: UsageIntent,
        momentum_memory: str,
    ) -> str:
        patches = self.payload.get("patches", ())
        if not isinstance(patches, Sequence) or isinstance(patches, (str, bytes)):
            raise ValueError("fixture patches must be an array")
        if not patches:
            return ""
        return str(patches[min(context.iteration, len(patches) - 1)])

    def update_momentum(
        self,
        context: MomentumUpdateContext,
        current_memory: str,
    ) -> Sequence[Mapping[str, Any]]:
        updates = self.payload.get("momentum_updates", ())
        if not isinstance(updates, Sequence) or isinstance(
            updates,
            (str, bytes),
        ):
            raise ValueError("fixture momentum_updates must be an array")
        if not updates:
            return ()
        selected = updates[min(context.iteration, len(updates) - 1)]
        if not isinstance(selected, Sequence) or isinstance(
            selected,
            (str, bytes),
        ):
            raise ValueError("fixture momentum update must be an array")
        return _validated_momentum_calls(selected)


class PromptedProductModel:
    """Share product workflow prompts across concrete text-generation transports."""

    def generate_tasks(
        self,
        request: SyntheticGenerationRequest,
    ) -> Sequence[Mapping[str, Any]]:
        payload = {
            "skill": request.package.files[request.package.entrypoint],
            "usage_intent": request.intent.to_dict(),
            "count": request.count,
            "coverage": request.coverage,
            "regeneration": request.regeneration,
        }
        system = (
            "你负责为 Skill 优化生成完整、异质且可验证的测试任务集合。"
            "当前 Skill 只是待测试、待优化的实现，不是任务领域的正确规格；"
            "Skill 中已有规则不得被当作事实或 ground truth。"
        )
        user = (
            f"返回恰好 {request.count} 项的 JSON 数组。先根据 Skill name/description、"
            "用户 intent 和你对该任务领域的一般知识，推断完成任务真正需要的能力；"
            "当前 Skill 是待测试、待优化的实现，不是正确规格。再设计能暴露当前实现缺口"
            "的自包含任务。不要因为某条规则出现在当前"
            " Skill 中就假定它正确，也不要只复述或验证 Skill 已写出的要求。"
            "任务必须能区分当前 Skill 与真正改进后的 Skill，而不是自证式检查。"
            f"在 {request.count} 项任务中，至少 {max(1, request.count // 2)} 项必须针对当前 Skill "
            f"缺失、模糊、可能错误或未覆盖的能力，至少 {min(2, request.count)} 项必须是边界或 adversarial case；其余任务也要"
            "覆盖真实领域能力。所有任务都必须留在该领域内；除非 intent 明确要求，"
            "不要加入无关的 arithmetic、tool-use planning 或其他跨领域任务。"
            "coverage 仅是覆盖维度提示，不能替代领域推理。"
            + (
                "这是 headroom retry：上一版任务使原始 Skill 平均得分超过 0.9；"
                "本次必须进一步提高难度，优先针对潜在缺口、歧义、干扰信息和边界行为。"
                if request.regeneration
                else ""
            )
            + "每项必须包含 prompt、capability、difficulty，"
            "并优先给出 evaluation_hint，字段名必须是 kind（禁止使用 "
            "mode）。programmatic 使用 kind 和 checks；reference 使用 kind、"
            "method，并在任务项给 expected_output；rubric_judge 只声明 kind，"
            "rubric 由后续独立步骤冻结；hybrid 使用 kind、components、weights，"
            "每个 component 都必须有 kind。"
            "程序化 check.type 只能是 exact、contains、not_contains、regex、"
            "json_valid、json_has_keys、min_length、max_length；reference "
            "method 只能是 exact、normalized_exact、numeric_tolerance、"
            "set_equality、token_f1。除 json_valid 外，每个 programmatic check "
            "都必须有 value：min_length/max_length 的 value 必须是整数，"
            "json_has_keys 的 value 必须是字符串数组，regex 的 value 必须是"
            "合法正则字符串。reference 必须同时提供 expected_output；"
            "numeric_tolerance 的 config.tolerance 必须是有限数字。hybrid 的 "
            "components 不得再嵌套 hybrid，weights 数量必须与 components 相同。"
            "只有唯一、确定的标准答案才能使用 exact、normalized_exact 或其他"
            " reference evaluator；开放式自然语言生成任务存在多种语义等价答案，"
            "必须使用 rubric_judge。只为可从任务输出稳定观察的性质设计程序化"
            "检查；不要为开放式任务编造唯一标准措辞。"
            "每个任务必须自包含：把完成任务所需的源材料、样例数据和约束直接"
            "写入 prompt，不得依赖测试环境中不存在的文件、仓库、网络资源、"
            "MCP 服务或专有工具。工具使用场景只能要求根据内联材料说明应采用的"
            "工具、参数与顺序，不得假设工具调用已经产生外部结果。"
            "不要引用尚未产生的 candidate。\n\n"
            + canonical_json(payload)
        )
        text = self._generate(system, user)
        try:
            return self._validated_generated_tasks(
                text,
                system=system,
                user=user,
                count=request.count,
            )
        except ValueError as first_error:
            coverage_retry = ""
            if "boundary, adversarial, or regression" in str(first_error):
                coverage_retry = (
                    "同时确保至少两个任务的 difficulty 明确标为 boundary、"
                    "adversarial 或 regression。"
                )
            if "at least two capabilities" in str(first_error):
                coverage_retry += "同时确保至少两个不同的领域 capability slug。"
            retry_user = (
                user
                + "\n\n上一份响应虽可能是合法 JSON，但不满足冻结前协议："
                + str(first_error)
                + "。请重新生成完整数组，严格修正该错误。"
                + coverage_retry
                + "只返回 JSON。"
            )
            retry = self._generate(system, retry_user)
            try:
                return self._validated_generated_tasks(
                    retry,
                    system=system,
                    user=retry_user,
                    count=request.count,
                )
            except ValueError as exc:
                try:
                    return self._validated_generated_tasks(
                        retry,
                        system=system,
                        user=retry_user,
                        count=request.count,
                        repair_evaluation=True,
                    )
                except ValueError as repaired_error:
                    raise ValueError(
                        "task generator returned an invalid task protocol: "
                        f"{repaired_error}"
                    ) from exc

    def plan_rubric(self, task: TaskDraft) -> Mapping[str, Any]:
        system = "你负责在 candidate 产生前冻结任务评分 rubric。"
        user = (
            "返回 JSON 对象，包含 dimensions（name、description、weight）"
            "和 0-1 scoring guidance。任务：\n" + task.to_json()
        )
        text = self._generate(system, user)
        try:
            result = self._parse_json_with_retry(
                text,
                system=system,
                user=user,
                label="rubric planner",
            )
            if not isinstance(result, Mapping):
                raise ValueError("rubric planner must return a JSON object")
            return dict(result)
        except ValueError as exc:
            return _fallback_rubric(task, reason=str(exc))

    def rollout(self, skill: str, task: OptimizationTask) -> Any:
        return self._generate(
            "严格按照以下 Skill 完成用户任务。\n\n" + skill,
            task.prompt,
        )

    def judge(
        self,
        task: OptimizationTask,
        output: Any,
        spec: EvaluationSpec,
    ) -> Mapping[str, Any]:
        payload = canonical_json(
            {
                "task": task.prompt,
                "output": output,
                "rubric": spec.rubric,
                "criteria": spec.criteria,
                "response_schema": {
                    "score": "number from 0 to 1",
                    "reason": "string",
                },
            }
        )
        system = "你是冻结 rubric 的评分器，只能按给定标准评分。"
        text = self._generate(system, payload)
        result = self._parse_json_with_retry(
            text,
            system=system,
            user=payload,
            label="judge",
        )
        if not isinstance(result, Mapping):
            raise ValueError("judge must return a JSON object")
        return dict(result)

    def propose_patch(
        self,
        context: IterationContext,
        feedback: IterationFeedback,
        intent: UsageIntent,
        momentum_memory: str,
    ) -> str:
        budget_section = build_budget_section(context.edit_budget)
        return self._generate(
            (
                "你是 SkillAdam optimizer。只返回针对 SKILL.md 的 unified diff；"
                "不要返回完整文件、解释、Markdown 代码块或省略号。第一、二行"
                "必须严格为 --- a/SKILL.md 和 +++ b/SKILL.md。每个 hunk 必须有"
                "合法的 @@ 行号头；上下文行与删除行必须逐字符复制 current_skill "
                "中的原始行（包括空格），不得改写或规范化。所有 hunk 都以同一份"
                " current_skill 为基线并可单独应用；相邻或重叠修改必须合并为一个"
                " hunk。遵守 edit_budget，并根据 previous_patch_error 生成新的可"
                "应用 patch。根据训练反馈只修复有证据的可泛化问题。"
            )
            + budget_section,
            canonical_json(
                {
                    "usage_intent": intent.to_dict(),
                    "current_skill": context.current_skill,
                    "ordered_trajectory_feedback": feedback.to_dict(),
                    "momentum": momentum_memory,
                    "previous_patch_error": context.previous_patch_error,
                    "edit_budget": context.edit_budget,
                },
            ),
        )

    def update_momentum(
        self,
        context: MomentumUpdateContext,
        current_memory: str,
    ) -> Sequence[Mapping[str, Any]]:
        tools = build_momentum_tools()
        system = (
            "你是 SkillAdam 的 Evolution-Informed Tracker 更新器。比较当前 Skill "
            "rollout、候选 patch、同批候选验证和 gate 结果，维护跨迭代问题记忆。"
            "只报告有 trajectory 或评测证据的问题；把本轮实际修改用 "
            "record_attempt 关联到对应问题；仅在验证证据充分时 resolve，复发时 "
            "reopen。返回 JSON 对象 {\"tool_calls\": [{\"name\": ..., "
            "\"arguments\": {...}}]}，调用顺序必须保证问题先存在再记录 attempt。"
        )
        user = canonical_json(
            {
                "current_problem_tracker": current_memory,
                "iteration_evidence": context.to_dict(),
                "available_tools": tools,
            }
        )
        parsed = self._parse_json_with_retry(
            self._generate(system, user),
            system=system,
            user=user,
            label="momentum updater",
        )
        if not isinstance(parsed, Mapping):
            raise ValueError("momentum updater must return a JSON object")
        calls = parsed.get("tool_calls")
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
            raise ValueError("momentum updater tool_calls must be an array")
        return _validated_momentum_calls(calls)

    def _validated_generated_tasks(
        self,
        text: str,
        *,
        system: str,
        user: str,
        count: int,
        repair_evaluation: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        result = self._parse_json_with_retry(
            text,
            system=system,
            user=user,
            label="task generator",
        )
        if isinstance(result, Mapping) and isinstance(result.get("tasks"), list):
            result = result["tasks"]
        if not isinstance(result, list):
            raise ValueError("task generator must return a JSON array")
        tasks = _task_mappings(result, "generated task")
        if len(tasks) != count:
            raise ValueError(
                f"task generator returned {len(tasks)} tasks; expected {count}"
            )
        if count >= 3:
            capabilities = {
                str(task.get("capability", "")).strip().lower()
                for task in tasks
            }
            if len(capabilities) < 2:
                raise ValueError(
                    "synthetic task pool must cover at least two capabilities"
                )
        if count == 8:
            boundary_count = sum(
                str(task.get("difficulty", "")).strip().lower()
                in {"boundary", "adversarial", "regression"}
                for task in tasks
            )
            if boundary_count < 2:
                raise ValueError(
                    "8-task synthetic pool must include at least two "
                    "boundary, adversarial, or regression tasks"
                )
        if repair_evaluation:
            tasks = _repair_generated_evaluations(tasks)
        drafts = ingest_task_records(tasks, source="synthetic")
        planner = EvaluationPlanner()
        for draft in drafts:
            planner.plan_task(draft)
        return tasks

    def _parse_json_with_retry(
        self,
        text: str,
        *,
        system: str,
        user: str,
        label: str,
    ) -> Any:
        try:
            return _json_value(text)
        except json.JSONDecodeError:
            retry = self._generate(
                system,
                user
                + "\n\n严格修正上一份响应，使其成为合法 JSON。"
                "只返回 JSON，不要解释或使用 Markdown 代码块。",
            )
            try:
                return _json_value(retry)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{label} returned invalid JSON") from exc

    def _generate(self, system: str, user: str) -> str:
        raise NotImplementedError


class OpenAICompatibleProductModel(PromptedProductModel):
    """Text model using the OpenAI Python SDK with an explicit endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url_env: str = "OPENAI_BASE_URL",
        wire_api: str = "completions",
        reasoning_effort: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "openai-compatible provider requires the backend extra"
            ) from exc
        self.model = model
        self._api_key_env = api_key_env
        self._base_url_env = base_url_env
        if wire_api not in {"completions", "responses"}:
            raise ValueError("wire_api must be completions or responses")
        self.wire_api = wire_api
        normalized_effort = (
            reasoning_effort.strip().lower() if reasoning_effort else None
        )
        if normalized_effort not in _REASONING_EFFORTS | {None}:
            raise ValueError(
                "reasoning_effort must be one of "
                + ", ".join(sorted(_REASONING_EFFORTS))
            )
        self.reasoning_effort = normalized_effort
        self._timeout = timeout
        self._open_ai = OpenAI
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        """Require credentials only on the first actual model call.

        Commands with no model calls, such as status and reject-all, need no API key.
        """

        if self._client is None:
            api_key = os.environ.get(self._api_key_env, "").strip()
            if not api_key:
                raise ValueError(
                    f"missing API key environment variable {self._api_key_env}"
                )
            self._client = self._open_ai(
                api_key=api_key,
                base_url=os.environ.get(self._base_url_env, "").strip() or None,
                timeout=self._timeout,
            )
        return self._client

    def _generate(self, system: str, user: str) -> str:
        if self.wire_api == "responses":
            request: dict[str, Any] = {
                "model": self.model,
                "instructions": system,
                "input": user,
            }
            if self.reasoning_effort:
                request["reasoning"] = {"effort": self.reasoning_effort}
            response = self.client.responses.create(**request)
            content = response.output_text
            if not isinstance(content, str) or not content.strip():
                raise ValueError("model returned empty text")
            return content.strip()
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(
            **request,
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("model returned empty text")
        return content.strip()


def _task_mappings(
    items: Sequence[Any],
    label: str,
) -> tuple[Mapping[str, Any], ...]:
    """Normalize a task array into a tuple of mappings.

    Model and fixture outputs are untrusted; dict(item) raises TypeError for scalars.
    Raise ValueError explicitly so CLI and MCP can return a structured input error.
    """

    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"{label} {index} must be an object, got "
                f"{type(item).__name__}"
            )
    return tuple(dict(item) for item in items)


def _repair_generated_evaluations(
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Preserve task content and fall back to rubric for evaluators still invalid after two generations.

    Fallback happens before candidate generation; generate and freeze the rubric separately.
    Record the repair reason in task metadata so protocol errors remain visible.
    """

    drafts = ingest_task_records(tasks, source="synthetic")
    planner = EvaluationPlanner()
    repaired: list[Mapping[str, Any]] = []
    for record, draft in zip(tasks, drafts, strict=True):
        try:
            planner.plan_task(draft)
            repaired.append(dict(record))
        except ValueError as exc:
            normalized = dict(record)
            normalized["evaluation_hint"] = {"kind": "rubric_judge"}
            metadata = dict(normalized.get("metadata", {}))
            metadata["generation_repair"] = {
                "field": "evaluation_hint",
                "reason": str(exc),
                "fallback": "rubric_judge",
            }
            normalized["metadata"] = metadata
            repaired.append(normalized)
    return tuple(repaired)


def _json_value(text: str) -> Any:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1])
    try:
        return json.loads(value)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        for index, character in enumerate(value):
            if character not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(value, index)
            except json.JSONDecodeError:
                continue
            return parsed
        raise original


def _validated_momentum_calls(
    calls: Sequence[Any],
) -> tuple[Mapping[str, Any], ...]:
    """Validate model-generated calls against the Momentum tool schema."""

    schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in build_momentum_tools()
    }
    normalized: list[Mapping[str, Any]] = []
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            raise ValueError(f"momentum tool call {index} must be an object")
        name = str(call.get("name", "")).strip()
        arguments = call.get("arguments")
        if name not in schemas:
            raise ValueError(f"unsupported momentum tool {name!r}")
        if not isinstance(arguments, Mapping):
            raise ValueError(
                f"momentum tool {name!r} arguments must be an object"
            )
        schema = schemas[name]
        required = set(schema["required"])
        allowed = set(schema["properties"])
        missing = required.difference(arguments)
        extra = set(arguments).difference(allowed)
        if missing or extra:
            raise ValueError(
                f"momentum tool {name!r} arguments mismatch; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        for argument_name, value in arguments.items():
            property_schema = schema["properties"][argument_name]
            expected_type = property_schema.get("type")
            valid = (
                expected_type == "string"
                and isinstance(value, str)
                or expected_type == "boolean"
                and isinstance(value, bool)
                or expected_type == "array"
                and isinstance(value, Sequence)
                and not isinstance(value, (str, bytes))
                and all(isinstance(item, str) for item in value)
            )
            if not valid:
                raise ValueError(
                    f"momentum tool {name!r} argument {argument_name!r} "
                    f"must have type {expected_type!r}"
                )
            enum = property_schema.get("enum")
            if enum is not None and value not in enum:
                raise ValueError(
                    f"momentum tool {name!r} argument {argument_name!r} "
                    "is outside the allowed enum"
                )
        normalized.append({"name": name, "arguments": dict(arguments)})
    return tuple(normalized)


def _fallback_rubric(task: TaskDraft, *, reason: str) -> Mapping[str, Any]:
    return {
        "dimensions": [
            {
                "name": "task_completion",
                "description": "是否完整交付任务明确要求的结果，而非只给计划。",
                "weight": 0.4,
            },
            {
                "name": "correctness_and_evidence",
                "description": "结论是否正确、具体，并受任务所给材料支持。",
                "weight": 0.35,
            },
            {
                "name": "constraint_following",
                "description": "是否遵守格式、边界、禁止项和其他显式约束。",
                "weight": 0.25,
            },
        ],
        "scoring_guidance": {
            "0": "核心任务未完成或结果不可用。",
            "0.5": "部分完成，但存在明显遗漏、错误或约束违反。",
            "1": "完整、正确且满足全部显式约束。",
        },
        "metadata": {
            "generation_repair": {
                "fallback": "task_general_rubric",
                "reason": reason,
            },
            "task_id": task.task_id,
        },
    }


__all__ = [
    "FixtureProductModel",
    "OpenAICompatibleProductModel",
    "PromptedProductModel",
    "ProductModel",
]
