"""Durable proposals and locks. An uncertain write never expires its lock."""

import contextlib
import json
import sqlite3

from .contracts import canonical, require


class Journal:
    def __init__(self, path):
        self.publication_path = str(path) + ".publication"
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS locks (
                resource TEXT PRIMARY KEY, proposal TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pages (
                node TEXT PRIMARY KEY, subject TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
                fingerprint TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS reads (
                owner TEXT NOT NULL, node TEXT NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY(owner, node));
        """)

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def publication(self):
        # Separate DB lock serializes the rare write/reconcile operations across
        # host processes without holding the proposal DB transaction over HTTP.
        guard = sqlite3.connect(self.publication_path, timeout=0, isolation_level=None)
        try:
            try:
                guard.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                require(False, "publisher_busy")
            yield
        finally:
            guard.close()

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, proposal_id):
        row = self.db.execute("SELECT body FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        require(row is not None, "proposal_not_found")
        return json.loads(row[0])

    def save(self, proposal):
        self.db.execute("INSERT INTO proposals VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                        (proposal["id"], canonical(proposal)))

    def pending(self, actor, now):
        for row in self.db.execute("SELECT body FROM proposals"):
            proposal = json.loads(row[0])
            # Approval expiry cannot resolve a claimed or possibly written action.
            if (proposal["actor"] == actor and
                    (proposal["state"] in ("claimed", "executing", "unknown") or
                     proposal["state"] == "pending" and proposal["expires"] > now)):
                return True
        return False

    def page(self, node):
        row = self.db.execute("SELECT subject,paused,fingerprint FROM pages WHERE node=?", (node,)).fetchone()
        require(row is not None, "page_not_published")
        return dict(row)

    def catalog_nodes(self, subject, after):
        # One extra key signals continuation without an extra remote lookup.
        return [row[0] for row in self.db.execute(
            "SELECT node FROM pages WHERE subject IN (?, 'team') AND node > ? ORDER BY node LIMIT 6",
            (subject, after))]

    def locked(self, resource):
        return self.db.execute("SELECT proposal FROM locks WHERE resource=?", (resource,)).fetchone()

    def lock(self, resource, proposal_id):
        require(not self.locked(resource), "page_busy")
        self.db.execute("INSERT INTO locks VALUES (?,?)", (resource, proposal_id))

    def release(self, proposal_id):
        self.db.execute("DELETE FROM locks WHERE proposal=?", (proposal_id,))

    def register(self, node, subject):
        self.db.execute("INSERT INTO pages(node,subject) VALUES (?,?) ON CONFLICT(node) DO NOTHING", (node, subject))
        require(self.page(node)["subject"] == subject, "subject_mismatch")

    def baseline(self, node, fingerprint):
        self.db.execute("UPDATE pages SET fingerprint=? WHERE node=?", (fingerprint, node))

    def observe(self, node, fingerprint):
        page = self.page(node)
        if page["fingerprint"] and page["fingerprint"] != fingerprint:
            self.db.execute("UPDATE pages SET paused=1 WHERE node=?", (node,))

    def pause(self, node, paused):
        with self.transaction():
            self.page(node)
            require(not self.locked(node), "page_busy")
            self.db.execute("UPDATE pages SET paused=? WHERE node=?", (int(paused), node))

    def remember(self, owner, node, fingerprint):
        self.db.execute("INSERT INTO reads VALUES (?,?,?) ON CONFLICT(owner,node) DO UPDATE SET fingerprint=excluded.fingerprint",
                        (owner, node, fingerprint))

    def seen(self, owner, node, fingerprint):
        row = self.db.execute("SELECT fingerprint FROM reads WHERE owner=? AND node=?", (owner, node)).fetchone()
        return row is not None and row[0] == fingerprint
