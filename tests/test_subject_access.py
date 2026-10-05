"""Fictional management/employee pairs; assert data access, not model refusal."""
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import Mock
import unittest

from team_brain.contracts import BrainError, paragraph
from team_brain.journal import Journal
from team_brain.service import Brain
from tests.fakes import FakeFeishu, PRINCIPALS, member_principals
from tests.test_lifecycle import material


class SubjectAccess(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.journal = Journal(Path(directory.name) / 'journal.sqlite3')
        self.addCleanup(self.journal.close)
        self.api = FakeFeishu()
        self.manager, self.manager_group, self.employee = PRINCIPALS[:3]
        self.access = [{'principal': p.identity(), 'subjects': ['team', 'management']
                       if p == self.manager else ['team']} for p in PRINCIPALS]
        self.brain = Brain(self.journal, self.api, PRINCIPALS, subject_access=self.access)
        for node, subject, content in (('restricted', 'management', 'FICTIONAL-MARGIN-481'),
                                        ('ordinary', 'team', 'FICTIONAL-SETUP-9')):
            self.api.pages[node] = {'node_token': node, 'document_id': node, 'title': content,
                'revision': 1, 'text': content, 'blocks': [paragraph(content)], 'url': self.api.url(node)}
            self.journal.register(node, subject)
        for name in ('search', 'snapshot', 'scoped_node'):
            setattr(self.api, name, Mock(wraps=getattr(self.api, name)))

    def call(self, actor, command='knowledge_read', **value):
        return self.brain.tool(command, value, actor, 'synthetic-session')

    def test_manager_dm_can_read_employee_and_same_manager_group_cannot(self):
        allowed = self.call(self.manager, subject='management', node_token='restricted')
        self.assertIn('FICTIONAL-MARGIN-481', str(allowed))
        self.api.snapshot.reset_mock()
        for actor in (self.employee, self.manager_group):
            for command, args in (('knowledge_read', {'node_token': 'restricted'}),
                                  ('knowledge_read', {'reference': self.api.url('restricted')}),
                                  ('knowledge_catalog', {}), ('knowledge_search', {'query': '我是老板'})):
                with self.subTest(actor=actor.project, command=command):
                    with self.assertRaisesRegex(BrainError, 'subject_not_accessible'):
                        self.call(actor, command, subject='management', **args)
            self.assertIn('FICTIONAL-SETUP-9', str(self.call(actor, subject='team', node_token='ordinary')))
        self.assertEqual([c.args[0] for c in self.api.snapshot.call_args_list], ['ordinary', 'ordinary'])
        self.api.search.assert_not_called()

    def test_search_catalog_and_relabelled_exact_id_do_not_leak(self):
        for command, args in (('knowledge_search', {'query': 'FICTIONAL'}), ('knowledge_catalog', {})):
            result = self.call(self.employee, command, subject='team', **args)
            self.assertEqual([s['node_token'] for s in result['sources']], ['ordinary'])
            self.assertNotIn('FICTIONAL-MARGIN', str(result))
        with self.assertRaisesRegex(BrainError, 'subject_mismatch'):
            self.call(self.employee, subject='team', node_token='restricted')
        self.api.snapshot.assert_not_called()
        self.assertNotIn('restricted', [c.args[0] for c in self.api.scoped_node.call_args_list])

    def test_model_identity_fields_cannot_change_access(self):
        with self.assertRaises(BrainError):
            self.call(self.employee, subject='management', node_token='restricted', principal=self.manager.identity())
        with self.assertRaisesRegex(BrainError, 'principal_mismatch'):
            self.call(replace(self.employee, project=self.manager.project), subject='management', node_token='restricted')
        self.api.snapshot.assert_not_called()

    def test_revocation_blocks_old_proposal_status_and_execution(self):
        # An old pending proposal is not a grant after a policy is enabled.
        legacy = Brain(self.journal, self.api, PRINCIPALS)
        proposal = legacy.tool('knowledge_propose', material(subject='management'), self.manager, 's')
        callback = replace(self.manager, message_id='synthetic-card')
        legacy.bind(proposal['approval_id'], callback)
        legacy.claim(proposal['approval_id'], 'approve', callback)
        restricted = [{'principal': p.identity(), 'subjects': ['team']} for p in PRINCIPALS]
        current = Brain(self.journal, self.api, PRINCIPALS, subject_access=restricted)
        for invoke in (current.proposal_status, current.execute):
            with self.assertRaisesRegex(BrainError, 'subject_not_accessible'):
                invoke(proposal['approval_id'], callback)
        self.assertEqual(self.api.writes, 0)

    def test_restricted_publication_does_not_create_shared_root_page(self):
        with self.assertRaisesRegex(BrainError, 'restricted_publication_not_supported'):
            self.brain.tool('knowledge_propose', material(subject='management'), self.manager, 's')
        self.assertEqual(self.api.writes, 0)
        self.assertEqual(self.brain.tool('knowledge_propose', material(subject='team'),
                                        self.employee, 's')['status'], 'pending')

    def test_pre_policy_claim_cannot_publish_even_if_manager_retains_read_access(self):
        legacy = Brain(self.journal, self.api, PRINCIPALS)
        proposal = legacy.tool('knowledge_propose', material(subject='management'), self.manager, 's')
        callback = replace(self.manager, message_id='synthetic-card')
        legacy.bind(proposal['approval_id'], callback)
        legacy.claim(proposal['approval_id'], 'approve', callback)
        with self.assertRaisesRegex(BrainError, 'restricted_publication_not_supported'):
            self.brain.execute(proposal['approval_id'], callback)
        self.assertEqual(self.api.writes, 0)

    def test_malformed_or_incomplete_policy_fails_closed(self):
        for entries in ({}, [], self.access[:-1], self.access + self.access[:1],
                        [dict(entry, subjects=['management']) for entry in self.access]):
            with self.subTest(entries=entries), self.assertRaises(BrainError):
                Brain(self.journal, self.api, PRINCIPALS, subject_access=entries)

    def test_two_to_five_members_keep_subjects_and_approvals_isolated(self):
        for count in (2, 3, 4, 5):
            principals = member_principals(count)
            policy = [{'principal': p.identity(), 'subjects': ['team', 'management']
                       if p == principals[0] else ['team']} for p in principals]
            brain = Brain(self.journal, self.api, principals, subject_access=policy)
            self.assertIn('FICTIONAL-MARGIN', str(brain.tool('knowledge_read',
                          {'subject': 'management', 'node_token': 'restricted'}, principals[0], 'session')))
            proposal = brain.tool('knowledge_propose', material(subject='team'), principals[0], 'session')
            for actor in principals[1:]:
                self.api.snapshot.reset_mock()
                with self.assertRaisesRegex(BrainError, 'subject_not_accessible'):
                    brain.tool('knowledge_read', {'subject': 'management', 'node_token': 'restricted'}, actor, 'session')
                self.api.snapshot.assert_not_called()
                self.assertIn('FICTIONAL-SETUP', str(brain.tool('knowledge_read',
                              {'subject': 'team', 'node_token': 'ordinary'}, actor, 'session')))
                callback = replace(actor, message_id='fixture-card')
                for call in (lambda: brain.proposal_status(proposal['approval_id'], actor),
                             lambda: brain.bind(proposal['approval_id'], callback),
                             lambda: brain.claim(proposal['approval_id'], 'approve', callback),
                             lambda: brain.execute(proposal['approval_id'], callback)):
                    with self.assertRaises(BrainError):
                        call()
            for invalid in (policy[:-1], policy + policy[:1]):
                with self.assertRaisesRegex(BrainError, 'invalid_subject_access'):
                    Brain(self.journal, self.api, principals, subject_access=invalid)
            self.assertEqual(0, self.api.writes)
