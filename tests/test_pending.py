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
        writes = self.api.writes
        self.assertEqual(self.pending(), {"pending": expected})
        self.assertEqual([tuple(r) for r in self.brain.journal.db.execute("SELECT body FROM proposals")],
                         [tuple(r) for r in rows])
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

    def test_expiry_including_claimed_is_projected_without_mutation(self):
        identifier = self.stage()
        self.brain.bind(identifier, self.callback)
        self.brain.claim(identifier, "approve", self.callback)
        for state in ("pending", "claimed"):
            p = self.brain.journal.get(identifier)
            p["state"] = state
            self.brain.journal.save(p)
            for offset, expected in ((-1, True), (0, False), (1, False)):
                self.now = p["expires"] + offset
                with self.subTest(state=state, offset=offset):
                    self.reopen()
                    self.assert_read_only_pending(expected)

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

    def test_terminal_and_uncertain_states_are_not_pending_approvals(self):
        identifier = self.stage()
        for state in ("cancelled", "needs_revision", "expired", "conflict", "verified", "executing", "unknown"):
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
