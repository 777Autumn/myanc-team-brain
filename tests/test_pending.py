from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from team_brain.contracts import BrainError
from team_brain.host import dispatch
from team_brain.journal import Journal
from team_brain.service import Brain
from tests.fakes import FakeFeishu, PRINCIPALS
from tests.test_lifecycle import material


class PendingActions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.api = FakeFeishu()
        self.now = 1_000_000
        self.actor = PRINCIPALS[0]
        self.callback = replace(self.actor, message_id="fictional-card")
        self.reopen()

    def tearDown(self):
        self.brain.journal.close()
        self.directory.cleanup()

    def reopen(self):
        if hasattr(self, "brain"):
            self.brain.journal.close()
        self.brain = Brain(Journal(self.path), self.api, PRINCIPALS, lambda: self.now)

    def stage(self, session="fictional-work"):
        return self.brain.tool("knowledge_propose", material(), self.actor, session)["approval_id"]

    def pending(self, principal=None):
        return dispatch(self.brain, {"operation": "pending", "principal": (principal or self.actor).identity()})

    def assert_read_only_pending(self, expected):
        rows = list(self.brain.journal.db.execute("SELECT body FROM proposals"))
        changes = self.brain.journal.db.total_changes
        writes = self.api.writes
        self.assertEqual(self.pending(), {"pending": expected})
        self.assertEqual([tuple(r) for r in self.brain.journal.db.execute("SELECT body FROM proposals")],
                         [tuple(r) for r in rows])
        self.assertEqual(self.brain.journal.db.total_changes, changes)
        self.assertEqual(self.api.writes, writes)

    def test_pending_claimed_verified_survive_restart_without_execution(self):
        self.assert_read_only_pending(False)
        identifier = self.stage()
        for state in ("pending", "claimed", "verified"):
            if state == "claimed":
                self.brain.bind(identifier, self.callback)
                self.brain.claim(identifier, "approve", self.callback)
            elif state == "verified":
                self.brain.execute(identifier, self.callback)
            self.assertEqual(self.brain.journal.get(identifier)["state"], state)
            with self.subTest(state=state):
                self.assert_read_only_pending(state != "verified")
                self.reopen()
                self.assert_read_only_pending(state != "verified")

    def test_pending_expiry_is_projected_without_mutation(self):
        identifier = self.stage()
        expires = self.brain.journal.get(identifier)["expires"]
        for offset, expected in ((-1, True), (0, False), (1, False)):
            self.now = expires + offset
            with self.subTest(offset=offset):
                self.reopen()
                self.assert_read_only_pending(expected)

    def test_expired_claim_blocks_until_reconcile_resolves_it(self):
        identifier = self.stage()
        self.brain.bind(identifier, self.callback)
        self.brain.claim(identifier, "approve", self.callback)
        self.now = self.brain.journal.get(identifier)["expires"] + 1
        self.reopen()
        self.assertEqual(self.brain.journal.get(identifier)["state"], "claimed")
        self.assert_read_only_pending(True)
        self.assertEqual(self.pending(PRINCIPALS[1]), {"pending": False})
        self.assertIsNotNone(self.brain.journal.locked("create:" + self.api.root_node))
        self.assertEqual(self.brain.reconcile(identifier)["status"], "expired")
        self.assertIsNone(self.brain.journal.locked("create:" + self.api.root_node))
        self.assert_read_only_pending(False)

    def test_executing_publication_blocks_even_after_expiry(self):
        identifier = self.stage()
        self.brain.bind(identifier, self.callback)
        self.brain.claim(identifier, "approve", self.callback)
        append = self.api.append
        observations = []

        def observe_execution(document_id, blocks, operation_id):
            self.now = self.brain.journal.get(identifier)["expires"] + 1
            journal = Journal(self.path)
            try:
                observer = Brain(journal, self.api, PRINCIPALS, lambda: self.now)
                observations.append((journal.get(identifier)["state"], observer.pending(self.actor),
                                     observer.pending(PRINCIPALS[1]), journal.locked(document_id) is not None))
            finally:
                journal.close()
            append(document_id, blocks, operation_id)

        self.api.append = observe_execution
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "verified")
        self.assertEqual(observations, [("executing", {"pending": True}, {"pending": False}, True)])
        self.assert_read_only_pending(False)

    def test_unknown_survives_restart_and_expiry_until_reconcile(self):
        identifier = self.stage()
        self.brain.bind(identifier, self.callback)
        self.brain.claim(identifier, "approve", self.callback)
        self.api.failure = "append"
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "unknown")
        for expired in (False, True):
            if expired:
                self.now = self.brain.journal.get(identifier)["expires"] + 1
            self.reopen()
            with self.subTest(expired=expired):
                self.assert_read_only_pending(True)
                self.assertEqual(self.pending(PRINCIPALS[1]), {"pending": False})
                self.assertIsNotNone(self.brain.journal.locked("create:" + self.api.root_node))
        writes = self.api.writes
        self.assertEqual(self.brain.reconcile(identifier)["status"], "verified")
        self.assertEqual(self.api.writes, writes)
        self.assertIsNone(self.brain.journal.locked("create:" + self.api.root_node))
        self.reopen()
        self.assert_read_only_pending(False)

    def test_exact_physical_principal_across_all_work_tokens(self):
        identifier = self.stage("earlier-work")
        self.brain.bind(identifier, self.callback)
        self.brain.claim(identifier, "cancel", self.callback)
        self.stage("another-work")
        self.reopen()
        self.assertEqual(self.pending(self.callback), {"pending": True})
        for other in PRINCIPALS[1:]:
            with self.subTest(project=other.project):
                self.assertEqual(self.pending(other), {"pending": False})
        for field in ("platform", "user_id", "chat_id", "session_key", "project"):
            other = replace(self.actor, **{field: "different"})
            self.brain.allowed.add(other.key)
            with self.subTest(field=field):
                self.assertEqual(self.pending(other), {"pending": False})
        with self.assertRaisesRegex(BrainError, "principal_mismatch"):
            self.pending(replace(self.actor, user_id="unconfigured"))

    def test_terminal_states_do_not_block(self):
        identifier = self.stage()
        for state in ("cancelled", "needs_revision", "expired", "conflict", "verified"):
            p = self.brain.journal.get(identifier)
            p["state"] = state
            self.brain.journal.save(p)
            with self.subTest(state=state):
                self.assert_read_only_pending(False)

    def test_pending_is_host_only_and_rejects_extra_arguments(self):
        self.stage()
        for key in ("session_token", "command", "input", "approval_id", "decision"):
            with self.subTest(key=key), self.assertRaises(BrainError):
                dispatch(self.brain, {"operation": "pending", "principal": self.actor.identity(), key: "extra"})
        for command in ("pending", "knowledge_pending"):
            with self.subTest(command=command), self.assertRaisesRegex(BrainError, "unsupported_command"):
                self.brain.tool(command, {}, self.actor, "model-token")
