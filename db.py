"""
SQLite database for Junk Filter MCP — zero-config persistence.
Replaces the PostgreSQL + Redis stack from the original full-stack version.

Uses autocommit mode (isolation_level=None) so every execute() is its own
implicit transaction — no explicit commit() calls needed.
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','paused','failed')),
    last_fetch_at REAL,
    error_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT DEFAULT '',
    published_at TEXT,
    created_at REAL NOT NULL,
    innovation_score INTEGER,
    depth_score INTEGER,
    decision TEXT,
    tldr TEXT,
    key_concepts TEXT,
    reasoning TEXT,
    evaluated_at REAL
);

CREATE TABLE IF NOT EXISTS eval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    innovation_score INTEGER NOT NULL,
    depth_score INTEGER NOT NULL,
    decision TEXT NOT NULL,
    tldr TEXT,
    key_concepts TEXT,
    reasoning TEXT,
    model TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_feed ON articles(feed_id);
CREATE INDEX IF NOT EXISTS idx_articles_decision ON articles(decision);
CREATE INDEX IF NOT EXISTS idx_articles_evaluated ON articles(evaluated_at);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(innovation_score, depth_score);
"""


class DB:
    """Thread-safe SQLite wrapper with autocommit."""

    def __init__(self, path: str = "junk_filter.db"):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.path), check_same_thread=False, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
        return self._conn

    # ── Feeds ──────────────────────────────────────────────────────

    def add_feed(self, url: str, name: str) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO feeds (url, name, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET name=excluded.name RETURNING id",
            (url, name, now),
        )
        return cur.fetchone()[0]

    def list_feeds(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, url, name, status, last_fetch_at, error_count, created_at "
            "FROM feeds ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_feed(self, feed_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM feeds WHERE id=?", (feed_id,)).fetchone()
        return dict(row) if row else None

    def update_feed_fetch(self, feed_id: int, success: bool):
        now = time.time()
        if success:
            self.conn.execute(
                "UPDATE feeds SET last_fetch_at=?, error_count=0, status='active' WHERE id=?",
                (now, feed_id),
            )
        else:
            self.conn.execute(
                "UPDATE feeds SET error_count=error_count+1, last_fetch_at=? WHERE id=?",
                (now, feed_id),
            )

    # ── Articles ───────────────────────────────────────────────────

    def upsert_article(
        self,
        feed_id: int,
        url: str,
        title: str,
        content: str,
        author: str = "",
        published_at: str = "",
    ) -> int:
        """Insert or ignore (dedup by url). Returns article id."""
        now = time.time()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO articles "
            "(feed_id, url, title, content, author, published_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (feed_id, url, title, content, author, published_at, now),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        existing = self.conn.execute(
            "SELECT id FROM articles WHERE url=?", (url,)
        ).fetchone()
        return existing[0] if existing else -1

    def get_unevaluated(self, feed_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE feed_id=? AND evaluated_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (feed_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_evaluation(
        self,
        article_id: int,
        innovation_score: int,
        depth_score: int,
        decision: str,
        tldr: str,
        key_concepts: List[str],
        reasoning: str,
        model: str = "",
    ):
        now = time.time()
        concepts_json = json.dumps(key_concepts, ensure_ascii=False)
        self.conn.execute(
            "UPDATE articles SET innovation_score=?, depth_score=?, decision=?, "
            "tldr=?, key_concepts=?, reasoning=?, evaluated_at=? WHERE id=?",
            (innovation_score, depth_score, decision, tldr,
             concepts_json, reasoning, now, article_id),
        )
        self.conn.execute(
            "INSERT INTO eval_log (article_id, innovation_score, depth_score, "
            "decision, tldr, key_concepts, reasoning, model, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (article_id, innovation_score, depth_score, decision,
             tldr, concepts_json, reasoning, model, now),
        )

    # ── Queries ────────────────────────────────────────────────────

    def top_articles(
        self, min_score: int = 6, limit: int = 10, decision: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conditions = (
            "evaluated_at IS NOT NULL AND (innovation_score + depth_score) / 2.0 >= ?"
        )
        params: list = [float(min_score)]
        if decision:
            conditions += " AND decision = ?"
            params.append(decision)
        rows = self.conn.execute(
            f"SELECT * FROM articles WHERE {conditions} "
            "ORDER BY (innovation_score + depth_score) DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_articles(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE title LIKE ? OR content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def article_count(self, feed_id: Optional[int] = None) -> int:
        if feed_id:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM articles WHERE feed_id=?", (feed_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()
        return row[0] if row else 0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
