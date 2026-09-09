import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops.bootstrap import register
from team_brain.contracts import BrainError
from team_brain.journal import Journal
from team_brain.service import Brain
from tests.fakes import FakeFeishu, PRINCIPALS


class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.directory.name) / 'brain.sqlite3')
        self.api = FakeFeishu()
        self.brain = Brain(self.journal, self.api, PRINCIPALS)
        self.seeds = []
        for number in range(8):
            node = self.api.create_node('Fictional seed ' + str(number))['node_token']
            self.seeds.append(dict(node_token=node, title=self.api.pages[node]['title'],
                                   raw_sha256=hashlib.sha256(b'').hexdigest(), subject='team'))
        self.api.writes = 0

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def count(self):
        return self.journal.db.execute('SELECT count(*) FROM pages').fetchone()[0]

    def test_eight_reviewed_pages_registered_once_without_remote_writes(self):
        self.assertEqual(register(self.brain, self.seeds), {'registered': 8})
        self.assertEqual(self.count(), 8)
        self.assertTrue(all(self.journal.page(s['node_token'])['fingerprint'] for s in self.seeds))
        with self.assertRaisesRegex(BrainError, 'bootstrap_requires_fresh_journal'):
            register(self.brain, self.seeds)
        self.assertEqual(self.api.writes, 0)

    def test_eighth_page_changed_does_not_register_first_seven(self):
        self.api.pages[self.seeds[-1]['node_token']]['title'] = 'Administrator changed it'
        with self.assertRaisesRegex(BrainError, 'approved_seed_changed'):
            register(self.brain, self.seeds)
        self.assertEqual(self.count(), 0)

    def test_local_failure_rolls_back_entire_registration(self):
        original = self.journal.baseline
        def interrupted(node, fingerprint):
            if node == self.seeds[-1]['node_token']:
                raise OSError('fictional disk failure')
            return original(node, fingerprint)
        with patch.object(self.journal, 'baseline', side_effect=interrupted):
            with self.assertRaises(OSError):
                register(self.brain, self.seeds)
        self.assertEqual(self.count(), 0)

    def test_existing_proposal_is_not_bootstrapped_over(self):
        self.journal.save({'id': 'fictional', 'state': 'unknown'})
        with self.assertRaisesRegex(BrainError, 'bootstrap_requires_fresh_journal'):
            register(self.brain, self.seeds)
        self.assertEqual(self.count(), 0)
