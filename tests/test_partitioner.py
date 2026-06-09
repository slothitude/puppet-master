"""Tests for partitioner.py — overlap classification, resolution, and partitioning."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mandate import CodeMandate
from partitioner import (
    OverlapConflict,
    OverlapResolution,
    OverlapType,
    _build_depends_edges,
    classify_overlap,
    partition_work,
    resolve_conflict,
)
from validator import _paths_overlap, detect_overlaps, detect_overlaps_enriched


# ── classify_overlap ──

class TestClassifyOverlap(unittest.TestCase):
    def test_absorb_subset(self):
        """m1 paths are subset of m2 → ABSORB."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/auth/**"],
            mandate_2_paths=["src/auth/**", "src/utils/**"],
            overlapping_paths=["src/auth/**"],
        )
        self.assertEqual(classify_overlap(c), OverlapType.ABSORB)

    def test_absorb_reverse(self):
        """m2 paths are subset of m1 → ABSORB."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/**"],
            mandate_2_paths=["src/auth/**"],
            overlapping_paths=["src/auth/**"],
        )
        self.assertEqual(classify_overlap(c), OverlapType.ABSORB)

    def test_extract_utility(self):
        """Overlap on shared utility directory → EXTRACT."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/shared/**", "src/auth/**"],
            mandate_2_paths=["src/shared/**", "src/api/**"],
            overlapping_paths=["src/shared/**"],
        )
        self.assertEqual(classify_overlap(c), OverlapType.EXTRACT)

    def test_extract_types(self):
        """Overlap on types file → EXTRACT."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/types.ts", "src/auth/**"],
            mandate_2_paths=["src/types.ts", "src/api/**"],
            overlapping_paths=["src/types.ts"],
        )
        self.assertEqual(classify_overlap(c), OverlapType.EXTRACT)

    def test_extract_config(self):
        """Overlap on config directory → EXTRACT."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["config/**", "src/auth/**"],
            mandate_2_paths=["config/**", "src/api/**"],
            overlapping_paths=["config/**"],
        )
        self.assertEqual(classify_overlap(c), OverlapType.EXTRACT)

    def test_sequence_collision(self):
        """Exact same paths, equal sets → SEQUENCE."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/auth/**"],
            mandate_2_paths=["src/auth/**"],
            overlapping_paths=["src/auth/**"],
        )
        self.assertEqual(classify_overlap(c), OverlapType.SEQUENCE)

    def test_no_overlap_not_tested_here(self):
        """No overlap means no conflict object — tested in partition_work."""
        pass


# ── resolve_conflict ──

class TestResolveConflict(unittest.TestCase):
    def test_resolve_absorb(self):
        """ABSORB resolution has absorbed_into + removed."""
        c = OverlapConflict(
            mandate_1_id="broad", mandate_2_id="narrow",
            mandate_1_paths=["src/**"],
            mandate_2_paths=["src/auth/**"],
            overlapping_paths=["src/auth/**"],
        )
        r = resolve_conflict(c)
        self.assertEqual(r.type, OverlapType.ABSORB)
        self.assertEqual(r.absorbed_into, "broad")
        self.assertEqual(r.removed, "narrow")

    def test_resolve_absorb_reverse(self):
        """When m2 is broader, m1 gets absorbed."""
        c = OverlapConflict(
            mandate_1_id="narrow", mandate_2_id="broad",
            mandate_1_paths=["src/auth/**"],
            mandate_2_paths=["src/**"],
            overlapping_paths=["src/auth/**"],
        )
        r = resolve_conflict(c)
        self.assertEqual(r.type, OverlapType.ABSORB)
        self.assertEqual(r.absorbed_into, "broad")
        self.assertEqual(r.removed, "narrow")

    def test_resolve_extract(self):
        """EXTRACT resolution has extracted_mandate_id + extracted_paths."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/shared/**", "src/auth/**"],
            mandate_2_paths=["src/shared/**", "src/api/**"],
            overlapping_paths=["src/shared/**"],
        )
        r = resolve_conflict(c)
        self.assertEqual(r.type, OverlapType.EXTRACT)
        self.assertIsNotNone(r.extracted_mandate_id)
        self.assertIn("src/shared/**", r.extracted_paths)
        self.assertIn("m1", r.depends_on_from)
        self.assertIn("m2", r.depends_on_from)

    def test_resolve_sequence(self):
        """SEQUENCE resolution has first + second."""
        c = OverlapConflict(
            mandate_1_id="m1", mandate_2_id="m2",
            mandate_1_paths=["src/auth/**"],
            mandate_2_paths=["src/auth/**"],
            overlapping_paths=["src/auth/**"],
        )
        r = resolve_conflict(c)
        self.assertEqual(r.type, OverlapType.SEQUENCE)
        self.assertEqual(r.first, "m1")
        self.assertEqual(r.second, "m2")


# ── partition_work ──

class TestPartitionWork(unittest.TestCase):
    def test_partition_no_overlaps(self):
        """Non-overlapping mandates → all survive."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/utils/**"])
        m3 = CodeMandate(mandate_id="m3", allowed_paths=["src/api/**"])
        plan = partition_work([m1, m2, m3])
        self.assertEqual(len(plan["active_mandates"]), 3)
        self.assertEqual(len(plan["resolutions"]), 0)
        self.assertEqual(plan["absorbed"], [])

    def test_partition_absorb_one(self):
        """Subset mandate gets absorbed."""
        m1 = CodeMandate(mandate_id="broad", allowed_paths=["src/**"])
        m2 = CodeMandate(mandate_id="narrow", allowed_paths=["src/auth/**"])
        plan = partition_work([m1, m2])
        ids = [m.mandate_id for m in plan["active_mandates"]]
        self.assertIn("broad", ids)
        self.assertNotIn("narrow", ids)
        self.assertIn("narrow", plan["absorbed"])
        self.assertEqual(len(plan["resolutions"]), 1)
        self.assertEqual(plan["resolutions"][0]["type"], "absorb")

    def test_partition_extract_shared(self):
        """Shared utility extracted to new mandate."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/shared/**", "src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/shared/**", "src/api/**"])
        plan = partition_work([m1, m2])
        # Originals should survive (not absorbed)
        ids = [m.mandate_id for m in plan["active_mandates"]]
        self.assertIn("m1", ids)
        self.assertIn("m2", ids)
        # A new extracted mandate should exist
        extract_ids = [m.mandate_id for m in plan["active_mandates"] if "extract" in m.mandate_id]
        self.assertEqual(len(extract_ids), 1)
        # depends_on edges: both originals → extracted
        self.assertEqual(len(plan["depends_on_edges"]), 2)

    def test_partition_sequence_collision(self):
        """Exact overlap creates depends_on edge."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/auth/**"])
        plan = partition_work([m1, m2])
        self.assertEqual(len(plan["active_mandates"]), 2)
        self.assertEqual(len(plan["resolutions"]), 1)
        self.assertEqual(plan["resolutions"][0]["type"], "sequence")
        # m2 depends on m1
        edges = plan["depends_on_edges"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["from"], "m2")
        self.assertEqual(edges[0]["to"], "m1")

    def test_partition_mixed(self):
        """Mix of ABSORB + EXTRACT + SEQUENCE in one batch."""
        broad = CodeMandate(mandate_id="broad", allowed_paths=["src/**"])
        narrow = CodeMandate(mandate_id="narrow", allowed_paths=["src/auth/**"])
        shared1 = CodeMandate(mandate_id="s1", allowed_paths=["src/lib/**", "src/api/**"])
        shared2 = CodeMandate(mandate_id="s2", allowed_paths=["src/lib/**", "src/ui/**"])
        dup1 = CodeMandate(mandate_id="d1", allowed_paths=["src/models/**"])
        dup2 = CodeMandate(mandate_id="d2", allowed_paths=["src/models/**"])
        plan = partition_work([broad, narrow, shared1, shared2, dup1, dup2])
        # narrow absorbed into broad
        self.assertIn("narrow", plan["absorbed"])
        # s1, s2, d1, d2 all survive (or have extract/sequence resolutions)
        ids = [m.mandate_id for m in plan["active_mandates"]]
        self.assertIn("broad", ids)
        # Extract for lib, sequence for models
        types = [r["type"] for r in plan["resolutions"]]
        self.assertIn("absorb", types)
        self.assertIn("extract", types)
        self.assertIn("sequence", types)


# ── _paths_overlap (glob-aware) ──

class TestPathsOverlap(unittest.TestCase):
    def test_glob_wildcard(self):
        """src/** overlaps src/auth/**."""
        self.assertTrue(_paths_overlap("src/**", "src/auth/**"))

    def test_glob_wildcard_reverse(self):
        """src/auth/** overlaps src/**."""
        self.assertTrue(_paths_overlap("src/auth/**", "src/**"))

    def test_exact_match(self):
        """Exact strings overlap."""
        self.assertTrue(_paths_overlap("src/auth/**", "src/auth/**"))

    def test_no_overlap(self):
        """Unrelated paths don't overlap."""
        self.assertFalse(_paths_overlap("src/auth/**", "src/utils/**"))

    def test_no_overlap_deep(self):
        """Deep unrelated paths don't overlap."""
        self.assertFalse(_paths_overlap("src/auth/login.py", "src/utils/helpers.py"))

    def test_partial_prefix_no_overlap(self):
        """Similar prefix but different directories."""
        self.assertFalse(_paths_overlap("src/app/**", "src/api/**"))


# ── detect_overlaps_enriched ──

class TestDetectOverlapsEnriched(unittest.TestCase):
    def test_glob_aware(self):
        """detect_overlaps_enriched catches src/** vs src/auth/**."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/auth/**"])
        conflicts = detect_overlaps_enriched([m1, m2])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].mandate_1_id, "m1")
        self.assertEqual(conflicts[0].mandate_2_id, "m2")

    def test_enriched_no_conflict(self):
        """No glob overlap → no conflict."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/utils/**"])
        conflicts = detect_overlaps_enriched([m1, m2])
        self.assertEqual(len(conflicts), 0)

    def test_existing_compat(self):
        """detect_overlaps (old) still works for exact matches."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/auth/**"])
        conflicts = detect_overlaps([m1, m2])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["mandate_1"], "m1")
        self.assertEqual(conflicts[0]["mandate_2"], "m2")

    def test_enriched_catches_more(self):
        """Enriched catches glob overlaps that exact-match misses."""
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/auth/**"])
        # Old detector misses this (no exact string match)
        old_conflicts = detect_overlaps([m1, m2])
        self.assertEqual(len(old_conflicts), 0)
        # New detector catches it
        new_conflicts = detect_overlaps_enriched([m1, m2])
        self.assertEqual(len(new_conflicts), 1)


# ── _build_depends_edges ──

class TestBuildDependsEdges(unittest.TestCase):
    def test_sequence_edge(self):
        r = OverlapResolution(
            type=OverlapType.SEQUENCE,
            conflict=OverlapConflict(
                mandate_1_id="a", mandate_2_id="b",
                mandate_1_paths=[], mandate_2_paths=[], overlapping_paths=[],
            ),
            first="a", second="b",
        )
        edges = _build_depends_edges([r])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["from"], "b")
        self.assertEqual(edges[0]["to"], "a")

    def test_extract_edges(self):
        r = OverlapResolution(
            type=OverlapType.EXTRACT,
            conflict=OverlapConflict(
                mandate_1_id="a", mandate_2_id="b",
                mandate_1_paths=[], mandate_2_paths=[], overlapping_paths=[],
            ),
            extracted_mandate_id="ext-1",
            extracted_paths=["src/shared/**"],
            depends_on_from=["a", "b"],
        )
        edges = _build_depends_edges([r])
        self.assertEqual(len(edges), 2)
        froms = {e["from"] for e in edges}
        self.assertEqual(froms, {"a", "b"})
        self.assertEqual(edges[0]["to"], "ext-1")


if __name__ == "__main__":
    unittest.main()
