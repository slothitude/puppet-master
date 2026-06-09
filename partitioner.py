"""Partitioner — overlap classification + resolution for mandates.

Classifies overlaps as ABSORB (containment), EXTRACT (shared utility),
or SEQUENCE (incidental collision), then resolves each with the correct
strategy. Only SEQUENCE needs an LLM call for ordering — the other two
are deterministic.

The key insight: "The judgment call shrinks dramatically if you classify
the overlap type first."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from mandate import CodeMandate


class OverlapType(Enum):
    ABSORB = "absorb"      # containment — one is subset of other
    EXTRACT = "extract"    # shared utility — extract to third mandate
    SEQUENCE = "sequence"  # incidental collision — serialize with depends_on


@dataclass
class OverlapConflict:
    mandate_1_id: str
    mandate_2_id: str
    mandate_1_paths: list[str]
    mandate_2_paths: list[str]
    overlapping_paths: list[str]
    overlap_type: OverlapType | None = None


@dataclass
class OverlapResolution:
    type: OverlapType
    conflict: OverlapConflict
    # ABSORB: which mandate survives
    absorbed_into: str | None = None       # mandate_id of the broader one
    removed: str | None = None             # mandate_id of the absorbed (removed) one
    # EXTRACT: third mandate for shared files
    extracted_mandate_id: str | None = None
    extracted_paths: list[str] = field(default_factory=list)
    depends_on_from: list[str] = field(default_factory=list)  # both originals depend on it
    # SEQUENCE: ordering
    first: str | None = None               # runs first
    second: str | None = None              # runs second, depends_on first
    note: str = ""


# Patterns that indicate shared/utility code
UTILITY_PATTERN = re.compile(
    r"(?i)(shared|common|util|types?|constants|config|base|core|helpers?|lib)"
)


def classify_overlap(conflict: OverlapConflict) -> OverlapType:
    """Classify an overlap into ABSORB, EXTRACT, or SEQUENCE.

    Deterministic heuristics — no LLM needed for classification.
    """
    s1 = set(conflict.mandate_1_paths)
    s2 = set(conflict.mandate_2_paths)

    # ABSORB: one set is a strict subset of the other (exact string match)
    if s1 < s2:
        return OverlapType.ABSORB
    if s2 < s1:
        return OverlapType.ABSORB

    # ABSORB via glob containment: check if one set of paths is "contained"
    # within the other. A path p1 is contained in p2 if p2's glob is a
    # superset of p1 (e.g. src/** contains src/auth/**).
    def _contains(p_broad: str, p_narrow: str) -> bool:
        """True if p_broad glob would match everything p_narrow matches."""
        if p_broad == p_narrow:
            return True
        broad_dir = p_broad.replace("/**", "").replace("/*", "")
        narrow_dir = p_narrow.replace("/**", "").replace("/*", "")
        # src/** contains src/auth/**, src/auth/login.py, etc.
        if broad_dir and narrow_dir.startswith(broad_dir):
            return True
        return False

    m1_is_subset = all(
        any(_contains(p2, p1) for p2 in conflict.mandate_2_paths)
        for p1 in conflict.mandate_1_paths
    ) and not all(
        any(_contains(p1, p2) for p1 in conflict.mandate_1_paths)
        for p2 in conflict.mandate_2_paths
    )
    m2_is_subset = all(
        any(_contains(p1, p2) for p1 in conflict.mandate_1_paths)
        for p2 in conflict.mandate_2_paths
    ) and not all(
        any(_contains(p2, p1) for p2 in conflict.mandate_2_paths)
        for p1 in conflict.mandate_1_paths
    )
    if m1_is_subset or m2_is_subset:
        return OverlapType.ABSORB

    # EXTRACT: overlap paths match utility patterns
    for path in conflict.overlapping_paths:
        for part in path.replace("**", "").split("/"):
            if UTILITY_PATTERN.search(part):
                return OverlapType.EXTRACT

    # SEQUENCE: everything else — incidental collision
    return OverlapType.SEQUENCE


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def resolve_conflict(conflict: OverlapConflict) -> OverlapResolution:
    """Resolve a classified overlap conflict."""
    conflict.overlap_type = classify_overlap(conflict)

    if conflict.overlap_type == OverlapType.ABSORB:
        # Broader mandate survives — check glob containment
        def _contains(p_broad: str, p_narrow: str) -> bool:
            if p_broad == p_narrow:
                return True
            broad_dir = p_broad.replace("/**", "").replace("/*", "")
            narrow_dir = p_narrow.replace("/**", "").replace("/*", "")
            if broad_dir and narrow_dir.startswith(broad_dir):
                return True
            return False

        m1_covers_m2 = all(
            any(_contains(p1, p2) for p1 in conflict.mandate_1_paths)
            for p2 in conflict.mandate_2_paths
        )
        if m1_covers_m2:
            absorbed_into, removed = conflict.mandate_1_id, conflict.mandate_2_id
        else:
            absorbed_into, removed = conflict.mandate_2_id, conflict.mandate_1_id
        return OverlapResolution(
            type=OverlapType.ABSORB,
            conflict=conflict,
            absorbed_into=absorbed_into,
            removed=removed,
            note=f"{removed} absorbed into {absorbed_into} (subset)",
        )

    elif conflict.overlap_type == OverlapType.EXTRACT:
        extracted_id = f"mnd-extract-{_now_ts()}"
        return OverlapResolution(
            type=OverlapType.EXTRACT,
            conflict=conflict,
            extracted_mandate_id=extracted_id,
            extracted_paths=list(conflict.overlapping_paths),
            depends_on_from=[conflict.mandate_1_id, conflict.mandate_2_id],
            note=f"Shared files extracted to {extracted_id}",
        )

    else:  # SEQUENCE — default: first by creation order
        return OverlapResolution(
            type=OverlapType.SEQUENCE,
            conflict=conflict,
            first=conflict.mandate_1_id,
            second=conflict.mandate_2_id,
            note=f"Sequential: {conflict.mandate_2_id} depends on {conflict.mandate_1_id}",
        )


def _resolution_to_dict(r: OverlapResolution) -> dict:
    return {
        "type": r.type.value,
        "mandate_1": r.conflict.mandate_1_id,
        "mandate_2": r.conflict.mandate_2_id,
        "overlapping_paths": r.conflict.overlapping_paths,
        "absorbed_into": r.absorbed_into,
        "removed": r.removed,
        "extracted_mandate_id": r.extracted_mandate_id,
        "extracted_paths": r.extracted_paths,
        "depends_on_from": r.depends_on_from,
        "first": r.first,
        "second": r.second,
        "note": r.note,
    }


def _build_depends_edges(resolutions: list[OverlapResolution]) -> list[dict]:
    """Build depends_on edges from SEQUENCE and EXTRACT resolutions."""
    edges = []
    for r in resolutions:
        if r.type == OverlapType.SEQUENCE and r.second and r.first:
            edges.append({"from": r.second, "to": r.first, "type": "depends_on"})
        elif r.type == OverlapType.EXTRACT and r.extracted_mandate_id:
            for mid in r.depends_on_from:
                edges.append({"from": mid, "to": r.extracted_mandate_id, "type": "depends_on"})
    return edges


def partition_work(mandates: list[CodeMandate], repo_path: str | None = None) -> dict:
    """Detect overlaps, classify, resolve. Returns plan for graph wiring.

    Returns a dict with:
    - active_mandates: mandates after ABSORB removals + EXTRACT creations
    - resolutions: list of resolution dicts
    - absorbed: list of absorbed mandate IDs
    - depends_on_edges: edges to wire into the graph
    """
    from validator import detect_overlaps_enriched

    # 1. Detect (glob-aware)
    conflicts = detect_overlaps_enriched(mandates)

    # 2. Classify + resolve each
    resolutions = [resolve_conflict(c) for c in conflicts]

    # 3. Apply ABSORB removals
    absorbed_ids: set[str] = set()
    for r in resolutions:
        if r.type == OverlapType.ABSORB and r.removed:
            absorbed_ids.add(r.removed)

    active_mandates = [m for m in mandates if m.mandate_id not in absorbed_ids]

    # 4. Apply EXTRACT — create new shared mandates, strip paths from originals
    extracted_mandates: list[CodeMandate] = []
    for r in resolutions:
        if r.type == OverlapType.EXTRACT and r.extracted_paths:
            shared = CodeMandate(
                mandate_id=r.extracted_mandate_id,
                goal=f"Shared: {r.extracted_paths}",
                allowed_paths=list(r.extracted_paths),
                parent_id="root",
            )
            extracted_mandates.append(shared)
            # Remove extracted paths from both originals
            for m in active_mandates:
                if m.mandate_id in r.depends_on_from:
                    m.allowed_paths = [
                        p for p in m.allowed_paths
                        if p not in r.extracted_paths
                    ]

    return {
        "active_mandates": active_mandates + extracted_mandates,
        "resolutions": [_resolution_to_dict(r) for r in resolutions],
        "absorbed": sorted(absorbed_ids),
        "depends_on_edges": _build_depends_edges(resolutions),
    }
