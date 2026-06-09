"""MandateGraph — SQLite WAL graph database for delegation tree.

Same architecture as GAT's gat.db: nodes + edges in SQLite,
queryable at runtime. Every agent at every depth talks here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class MandateGraph:
    def __init__(self, db_path: str = "data/mandate_graph.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        c = self._conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS mandate_nodes (
                id          TEXT PRIMARY KEY,
                node_type   TEXT NOT NULL,
                data        TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mandate_edges (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                src         TEXT NOT NULL,
                dst         TEXT NOT NULL,
                edge_type   TEXT NOT NULL,
                data        TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (src) REFERENCES mandate_nodes(id),
                FOREIGN KEY (dst) REFERENCES mandate_nodes(id)
            );

            CREATE INDEX IF NOT EXISTS idx_edges_src    ON mandate_edges(src);
            CREATE INDEX IF NOT EXISTS idx_edges_dst    ON mandate_edges(dst);
            CREATE INDEX IF NOT EXISTS idx_edges_type   ON mandate_edges(edge_type);
            CREATE INDEX IF NOT EXISTS idx_nodes_type   ON mandate_nodes(node_type);

            CREATE TABLE IF NOT EXISTS mandate_fts_data (
                rowid INTEGER PRIMARY KEY,
                mandate_id TEXT,
                goal TEXT,
                allowed_paths TEXT,
                status TEXT
            );
        """)
        self._conn.commit()

    # --- Node operations ---

    def add_node(self, node_id: str, node_type: str, data: dict | None = None) -> None:
        from mandate import _now
        now = _now()
        self._conn.execute(
            """INSERT OR REPLACE INTO mandate_nodes (id, node_type, data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (node_id, node_type, json.dumps(data or {}), now, now),
        )
        # Update FTS
        if node_type == "mandate":
            goal = data.get("goal", "") if data else ""
            paths = ",".join(data.get("allowed_paths", [])) if data else ""
            status = data.get("status", "") if data else ""
            self._conn.execute(
                """INSERT OR REPLACE INTO mandate_fts_data (rowid, mandate_id, goal, allowed_paths, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (hash(node_id), node_id, goal, paths, status),
            )
        self._conn.commit()

    def get_node(self, node_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, node_type, data, created_at, updated_at FROM mandate_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "node_type": row[1],
            "data": json.loads(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
        }

    def update_node(self, node_id: str, data: dict) -> None:
        from mandate import _now
        existing = self.get_node(node_id)
        if not existing:
            raise KeyError(f"Node {node_id} not found")
        merged = {**existing["data"], **data}
        self._conn.execute(
            "UPDATE mandate_nodes SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(merged), _now(), node_id),
        )
        # Update FTS if mandate
        if existing["node_type"] == "mandate":
            self._conn.execute(
                """INSERT OR REPLACE INTO mandate_fts_data (rowid, mandate_id, goal, allowed_paths, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (hash(node_id), node_id, data.get("goal", ""), ",".join(data.get("allowed_paths", [])), data.get("status", "")),
            )
        self._conn.commit()

    def delete_node(self, node_id: str) -> None:
        self._conn.execute("DELETE FROM mandate_edges WHERE src = ? OR dst = ?", (node_id, node_id))
        self._conn.execute("DELETE FROM mandate_nodes WHERE id = ?", (node_id,))
        self._conn.execute("DELETE FROM mandate_fts_data WHERE mandate_id = ?", (node_id,))
        self._conn.commit()

    # --- Edge operations ---

    def add_edge(self, src: str, dst: str, edge_type: str, data: dict | None = None) -> int:
        from mandate import _now
        cur = self._conn.execute(
            """INSERT INTO mandate_edges (src, dst, edge_type, data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (src, dst, edge_type, json.dumps(data or {}), _now()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore

    def get_edges(self, src: str | None = None, dst: str | None = None, edge_type: str | None = None) -> list[dict]:
        query = "SELECT id, src, dst, edge_type, data, created_at FROM mandate_edges WHERE 1=1"
        params: list[Any] = []
        if src:
            query += " AND src = ?"
            params.append(src)
        if dst:
            query += " AND dst = ?"
            params.append(dst)
        if edge_type:
            query += " AND edge_type = ?"
            params.append(edge_type)
        rows = self._conn.execute(query, params).fetchall()
        return [
            {"id": r[0], "src": r[1], "dst": r[2], "edge_type": r[3], "data": json.loads(r[4]), "created_at": r[5]}
            for r in rows
        ]

    def delete_edge(self, edge_id: int) -> None:
        self._conn.execute("DELETE FROM mandate_edges WHERE id = ?", (edge_id,))
        self._conn.commit()

    # --- Graph traversal ---

    def find_ancestry(self, node_id: str) -> list[str]:
        """Walk up delegates_to edges to find full chain to root."""
        chain = [node_id]
        current = node_id
        while True:
            edges = self.get_edges(dst=current, edge_type="delegates_to")
            if not edges:
                break
            parent = edges[0]["src"]
            chain.append(parent)
            current = parent
        return chain

    def find_children(self, node_id: str, edge_type: str = "delegates_to") -> list[str]:
        """Find direct children via given edge type."""
        edges = self.get_edges(src=node_id, edge_type=edge_type)
        return [e["dst"] for e in edges]

    def find_subtree(self, root_id: str, max_depth: int = 10) -> dict:
        """BFS to find entire subtree under root."""
        visited: dict[str, dict] = {}
        queue = [(root_id, 0)]
        while queue:
            nid, depth = queue.pop(0)
            if nid in visited or depth > max_depth:
                continue
            node = self.get_node(nid)
            if not node:
                continue
            visited[nid] = {**node, "depth": depth}
            children = self.find_children(nid)
            for child in children:
                queue.append((child, depth + 1))
        return visited

    def find_by_status(self, status: str) -> list[dict]:
        """Find all mandate nodes with given status."""
        rows = self._conn.execute(
            "SELECT id, node_type, data, created_at, updated_at FROM mandate_nodes WHERE node_type = 'mandate' AND json_extract(data, '$.status') = ?",
            (status,),
        ).fetchall()
        return [
            {"id": r[0], "node_type": r[1], "data": json.loads(r[2]), "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    def find_by_type(self, node_type: str) -> list[dict]:
        """Find all nodes of a given type."""
        rows = self._conn.execute(
            "SELECT id, node_type, data, created_at, updated_at FROM mandate_nodes WHERE node_type = ?",
            (node_type,),
        ).fetchall()
        return [
            {"id": r[0], "node_type": r[1], "data": json.loads(r[2]), "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    # --- Search ---

    def fts_search(self, query: str) -> list[dict]:
        """Full-text search across mandate goals and paths."""
        rows = self._conn.execute(
            """SELECT mandate_id, goal, allowed_paths, status
               FROM mandate_fts_data
               WHERE mandate_id MATCH ? OR goal MATCH ? OR allowed_paths MATCH ? OR status MATCH ?""",
            (query, query, query, query),
        ).fetchall()
        results = []
        for r in rows:
            node = self.get_node(r[0])
            if node:
                results.append(node)
        return results

    def graph_stats(self) -> dict:
        """Return statistics about the graph."""
        nodes = self._conn.execute("SELECT COUNT(*) FROM mandate_nodes").fetchone()[0]
        edges = self._conn.execute("SELECT COUNT(*) FROM mandate_edges").fetchone()[0]
        types = self._conn.execute("SELECT node_type, COUNT(*) FROM mandate_nodes GROUP BY node_type").fetchall()
        statuses = self._conn.execute(
            "SELECT json_extract(data, '$.status'), COUNT(*) FROM mandate_nodes WHERE node_type = 'mandate' GROUP BY json_extract(data, '$.status')"
        ).fetchall()
        return {
            "total_nodes": nodes,
            "total_edges": edges,
            "by_type": {t[0]: t[1] for t in types},
            "by_status": {s[0]: s[1] for s in statuses},
        }

    # --- Cleanup ---

    def close(self) -> None:
        self._conn.close()
