"""Patch proposals, user choices, and selected candidate assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from skilladam.core.diff_apply import (
    HUNK_SCHEMA_VERSION,
    PatchApplyError,
    PatchHunk,
    apply_patch_hunks,
    parse_patch_hunks,
)
from skilladam.product.models import (
    PublicModel,
    SCHEMA_VERSION,
    _freeze,
    _text,
    digest_value,
)


PATCH_PARSER_VERSION = "1"


class PatchReviewError(ValueError):
    """A patch proposal or selection violates the public protocol."""


@dataclass(frozen=True, slots=True)
class PatchProposal(PublicModel):
    proposal_id: str
    base_digest: str
    raw_diff: str
    hunks: tuple[PatchHunk, ...]
    mutable_paths: tuple[str, ...]
    parser_version: str = PATCH_PARSER_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.hunks:
            raise PatchReviewError("patch proposal must contain at least one hunk")
        if tuple(hunk.order for hunk in self.hunks) != tuple(range(len(self.hunks))):
            raise PatchReviewError("patch hunks must keep contiguous original order")
        if len({hunk.hunk_id for hunk in self.hunks}) != len(self.hunks):
            raise PatchReviewError("patch hunk IDs must be unique")
        if any(hunk.target_path not in self.mutable_paths for hunk in self.hunks):
            raise PatchReviewError("patch targets a path outside mutable_paths")
        if any(hunk.schema_version != HUNK_SCHEMA_VERSION for hunk in self.hunks):
            raise PatchReviewError("unsupported hunk schema version")


@dataclass(frozen=True, slots=True)
class PatchSelection(PublicModel):
    proposal_id: str
    accepted_hunk_ids: tuple[str, ...]
    rejected_hunk_ids: tuple[str, ...]
    actor: str = "user"
    reason: str = ""
    idempotency_key: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        accepted = tuple(self.accepted_hunk_ids)
        rejected = tuple(self.rejected_hunk_ids)
        all_ids = accepted + rejected
        if len(all_ids) != len(set(all_ids)):
            raise PatchReviewError("selection hunk IDs must be unique and disjoint")
        object.__setattr__(self, "proposal_id", _text(self.proposal_id, "proposal_id"))
        object.__setattr__(self, "accepted_hunk_ids", accepted)
        object.__setattr__(self, "rejected_hunk_ids", rejected)
        object.__setattr__(self, "actor", _text(self.actor, "actor"))


@dataclass(frozen=True, slots=True)
class CandidateSnapshot(PublicModel):
    base_digest: str
    proposal_id: str
    applied_hunk_ids: tuple[str, ...]
    files: Mapping[str, str]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.applied_hunk_ids:
            raise PatchReviewError("candidate requires at least one applied hunk")
        object.__setattr__(self, "files", _freeze(self.files))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def candidate_digest(self) -> str:
        return digest_value(self.files)


def prepare_patch_proposal(
    base_files: Mapping[str, str],
    raw_diff: str,
    *,
    mutable_paths: Iterable[str] | None = None,
) -> PatchProposal:
    """Freeze an optimizer unified diff as a reviewable proposal."""

    normalized_files = _normalize_files(base_files)
    base_digest = digest_value(normalized_files)
    normalized_diff = raw_diff.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_diff:
        raise PatchReviewError("raw_diff must not be empty")
    allowed = tuple(
        _normalize_path(path)
        for path in (mutable_paths if mutable_paths is not None else normalized_files)
    )
    if not allowed or len(allowed) != len(set(allowed)):
        raise PatchReviewError("mutable_paths must be non-empty and unique")
    hunks = parse_patch_hunks(
        normalized_diff,
        base_digest=base_digest,
        default_target=allowed[0],
        normalize_counts=True,
    )
    if not hunks:
        raise PatchReviewError("raw_diff contains no valid hunks")
    if any(hunk.target_path not in allowed for hunk in hunks):
        raise PatchReviewError("raw_diff targets a path outside mutable_paths")
    proposal_payload = {
        "base_digest": base_digest,
        "mutable_paths": allowed,
        "parser_version": PATCH_PARSER_VERSION,
        "raw_diff": normalized_diff,
        "schema_version": SCHEMA_VERSION,
    }
    return PatchProposal(
        proposal_id=f"proposal_{digest_value(proposal_payload)}",
        base_digest=base_digest,
        raw_diff=normalized_diff,
        hunks=hunks,
        mutable_paths=allowed,
    )


def select_patch_hunks(
    proposal: PatchProposal,
    accepted_hunk_ids: Iterable[str],
    *,
    actor: str = "user",
    reason: str = "",
    idempotency_key: str = "",
) -> PatchSelection:
    """Build explicit choices for every proposal hunk from accepted IDs."""

    accepted = tuple(accepted_hunk_ids)
    if len(accepted) != len(set(accepted)):
        raise PatchReviewError("accepted hunk IDs must not contain duplicates")
    known = tuple(hunk.hunk_id for hunk in proposal.hunks)
    unknown = set(accepted).difference(known)
    if unknown:
        raise PatchReviewError(
            f"selection contains unknown hunk IDs: {sorted(unknown)}"
        )
    accepted_set = set(accepted)
    ordered_accepted = tuple(item for item in known if item in accepted_set)
    rejected = tuple(item for item in known if item not in accepted_set)
    return PatchSelection(
        proposal_id=proposal.proposal_id,
        accepted_hunk_ids=ordered_accepted,
        rejected_hunk_ids=rejected,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )


def selected_patch_diff(
    proposal: PatchProposal,
    accepted_hunk_ids: Iterable[str],
) -> str:
    """Serialize applied hunks for EIT/Momentum attribution."""

    accepted = tuple(accepted_hunk_ids)
    if len(accepted) != len(set(accepted)):
        raise PatchReviewError("accepted hunk IDs must not contain duplicates")
    known = tuple(hunk.hunk_id for hunk in proposal.hunks)
    unknown = set(accepted).difference(known)
    if unknown:
        raise PatchReviewError(
            f"selection contains unknown hunk IDs: {sorted(unknown)}"
        )
    accepted_set = set(accepted)
    selected = tuple(
        hunk for hunk in proposal.hunks if hunk.hunk_id in accepted_set
    )
    if not selected:
        return ""
    if tuple(hunk.hunk_id for hunk in selected) == known:
        return proposal.raw_diff
    return render_patch_hunks(selected)


def render_patch_hunks(hunks: Iterable[PatchHunk]) -> str:
    """Render public PatchHunk objects as a unified diff in original order."""

    parts: list[str] = []
    previous_target = ""
    for hunk in hunks:
        if hunk.target_path != previous_target:
            parts.extend(
                (
                    f"--- a/{hunk.target_path}",
                    f"+++ b/{hunk.target_path}",
                )
            )
            previous_target = hunk.target_path
        parts.append(
            f"@@ -{hunk.old_start},{hunk.old_count} "
            f"+{hunk.new_start},{hunk.new_count} @@"
        )
        parts.extend(
            f"{operation}{line}" for operation, line in hunk.operations
        )
    return "\n".join(parts)


def apply_patch_selection(
    base_files: Mapping[str, str],
    proposal: PatchProposal,
    selection: PatchSelection,
) -> CandidateSnapshot | None:
    """Strictly and atomically assemble the candidate selected by the user."""

    normalized_files = _normalize_files(base_files)
    current_digest = digest_value(normalized_files)
    if current_digest != proposal.base_digest:
        raise PatchReviewError("base files are stale for this proposal")
    expected = prepare_patch_proposal(
        normalized_files,
        proposal.raw_diff,
        mutable_paths=proposal.mutable_paths,
    )
    if expected.to_dict() != proposal.to_dict():
        raise PatchReviewError("patch proposal failed integrity validation")
    _validate_selection(proposal, selection)
    if not selection.accepted_hunk_ids:
        return None

    selected_ids = set(selection.accepted_hunk_ids)
    selected = tuple(hunk for hunk in proposal.hunks if hunk.hunk_id in selected_ids)
    candidate_files = dict(normalized_files)
    for target_path in dict.fromkeys(hunk.target_path for hunk in selected):
        target_hunks = tuple(hunk for hunk in selected if hunk.target_path == target_path)
        original = candidate_files.get(target_path, "")
        try:
            candidate_files[target_path] = apply_patch_hunks(original, target_hunks)
        except PatchApplyError as exc:
            raise PatchReviewError(
                f"selected hunks cannot be applied to {target_path}: {exc}"
            ) from exc
    return CandidateSnapshot(
        base_digest=proposal.base_digest,
        proposal_id=proposal.proposal_id,
        applied_hunk_ids=tuple(hunk.hunk_id for hunk in selected),
        files=MappingProxyType(candidate_files),
        metadata={"selection_digest": selection.digest},
    )


def _validate_selection(
    proposal: PatchProposal,
    selection: PatchSelection,
) -> None:
    if selection.proposal_id != proposal.proposal_id:
        raise PatchReviewError("selection belongs to a different proposal")
    known = tuple(hunk.hunk_id for hunk in proposal.hunks)
    selected = selection.accepted_hunk_ids + selection.rejected_hunk_ids
    if len(selected) != len(known) or set(selected) != set(known):
        raise PatchReviewError("selection must classify every proposal hunk exactly once")


def _normalize_files(base_files: Mapping[str, str]) -> dict[str, str]:
    normalized = {
        _normalize_path(path): content
        for path, content in base_files.items()
    }
    if not normalized:
        raise PatchReviewError("base_files must not be empty")
    if any(not isinstance(content, str) for content in normalized.values()):
        raise PatchReviewError("base file content must be text")
    return normalized


def _normalize_path(path: str) -> str:
    normalized = _text(path, "path").replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise PatchReviewError("paths must be relative and may not traverse parents")
    return normalized


__all__ = [
    "CandidateSnapshot",
    "PATCH_PARSER_VERSION",
    "PatchHunk",
    "PatchProposal",
    "PatchReviewError",
    "PatchSelection",
    "apply_patch_selection",
    "prepare_patch_proposal",
    "render_patch_hunks",
    "selected_patch_diff",
    "select_patch_hunks",
]
