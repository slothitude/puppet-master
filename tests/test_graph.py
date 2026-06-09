"""Tests for graph.py — node/edge ops, ancestry traversal, FTS search."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph import MandateGraph


class TestNodeOps(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph = MandateGraph(os.path.join(self.tmpdir, "test.db"))

    def tearDown(self):
        self.graph.close()

    def test_add_get_node(self):
        self.graph.add_node("n1", "mandate", {"goal": "fix auth", "status": "pending"})
        node = self.graph.get_node("n1")
        self.assertIsNotNone(node)
        self.assertEqual(node["node_type"], "mandate")
        self.assertEqual(node["data"]["goal"], "fix auth")

    def test_update_node(self):
        self.graph.add_node("n1", "mandate", {"status": "pending"})
        self.graph.update_node("n1", {"status": "dispatched"})
        node = self.graph.get_node("n1")
        self.assertEqual(node["data"]["status"], "dispatched")

    def test_delete_node(self):
        self.graph.add_node("n1", "mandate", {})
        self.graph.add_node("n2", "mandate", {})
        self.graph.add_edge("n1", "n2", "delegates_to")
        self.graph.delete_node("n1")
        self.assertIsNone(self.graph.get_node("n1"))
        edges = self.graph.get_edges(src="n1")
        self.assertEqual(len(edges), 0)

    def test_node_not_found(self):
        self.assertIsNone(self.graph.get_node("nonexistent"))


class TestEdgeOps(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph = MandateGraph(os.path.join(self.tmpdir, "test.db"))
        self.graph.add_node("n1", "mandate", {})
        self.graph.add_node("n2", "mandate", {})
        self.graph.add_node("n3", "mandate", {})

    def tearDown(self):
        self.graph.close()

    def test_add_get_edge(self):
        eid = self.graph.add_edge("n1", "n2", "delegates_to")
        edges = self.graph.get_edges(src="n1")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["edge_type"], "delegates_to")

    def test_get_edges_by_dst(self):
        self.graph.add_edge("n1", "n2", "delegates_to")
        edges = self.graph.get_edges(dst="n2")
        self.assertEqual(len(edges), 1)

    def test_get_edges_by_type(self):
        self.graph.add_edge("n1", "n2", "delegates_to")
        self.graph.add_edge("n2", "n3", "depends_on")
        edges = self.graph.get_edges(edge_type="delegates_to")
        self.assertEqual(len(edges), 1)

    def test_delete_edge(self):
        eid = self.graph.add_edge("n1", "n2", "delegates_to")
        self.graph.delete_edge(eid)
        edges = self.graph.get_edges(src="n1")
        self.assertEqual(len(edges), 0)


class TestAncestry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph = MandateGraph(os.path.join(self.tmpdir, "test.db"))
        self.graph.add_node("root", "mandate", {})
        self.graph.add_node("m1", "mandate", {})
        self.graph.add_node("m2", "mandate", {})
        self.graph.add_edge("root", "m1", "delegates_to")
        self.graph.add_edge("m1", "m2", "delegates_to")

    def tearDown(self):
        self.graph.close()

    def test_find_ancestry(self):
        chain = self.graph.find_ancestry("m2")
        self.assertEqual(chain, ["m2", "m1", "root"])

    def test_find_ancestry_root(self):
        chain = self.graph.find_ancestry("root")
        self.assertEqual(chain, ["root"])

    def test_find_children(self):
        children = self.graph.find_children("root")
        self.assertEqual(children, ["m1"])
        children2 = self.graph.find_children("m1")
        self.assertEqual(children2, ["m2"])


class TestSubtree(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph = MandateGraph(os.path.join(self.tmpdir, "test.db"))
        for nid in ["root", "m1", "m2", "m3"]:
            self.graph.add_node(nid, "mandate", {})
        self.graph.add_edge("root", "m1", "delegates_to")
        self.graph.add_edge("root", "m2", "delegates_to")
        self.graph.add_edge("m1", "m3", "delegates_to")

    def tearDown(self):
        self.graph.close()

    def test_subtree(self):
        tree = self.graph.find_subtree("root")
        self.assertIn("root", tree)
        self.assertIn("m1", tree)
        self.assertIn("m2", tree)
        self.assertIn("m3", tree)
        self.assertEqual(tree["root"]["depth"], 0)
        self.assertEqual(tree["m1"]["depth"], 1)
        self.assertEqual(tree["m3"]["depth"], 2)

    def test_partial_subtree(self):
        tree = self.graph.find_subtree("m1")
        self.assertNotIn("root", tree)
        self.assertIn("m1", tree)
        self.assertIn("m3", tree)


class TestFindByStatus(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph = MandateGraph(os.path.join(self.tmpdir, "test.db"))
        self.graph.add_node("m1", "mandate", {"status": "pending"})
        self.graph.add_node("m2", "mandate", {"status": "dispatched"})
        self.graph.add_node("m3", "mandate", {"status": "pending"})
        self.graph.add_node("v1", "violation", {})

    def tearDown(self):
        self.graph.close()

    def test_find_pending(self):
        nodes = self.graph.find_by_status("pending")
        self.assertEqual(len(nodes), 2)

    def test_find_by_type(self):
        nodes = self.graph.find_by_type("violation")
        self.assertEqual(len(nodes), 1)


class TestGraphStats(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.graph = MandateGraph(os.path.join(self.tmpdir, "test.db"))
        self.graph.add_node("m1", "mandate", {"status": "pending"})
        self.graph.add_node("m2", "mandate", {"status": "dispatched"})
        self.graph.add_node("v1", "violation", {})
        self.graph.add_edge("m1", "m2", "delegates_to")

    def tearDown(self):
        self.graph.close()

    def test_stats(self):
        stats = self.graph.graph_stats()
        self.assertEqual(stats["total_nodes"], 3)
        self.assertEqual(stats["total_edges"], 1)
        self.assertEqual(stats["by_type"]["mandate"], 2)
        self.assertEqual(stats["by_type"]["violation"], 1)


if __name__ == "__main__":
    unittest.main()
