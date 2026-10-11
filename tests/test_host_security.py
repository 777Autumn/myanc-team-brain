import json
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from team_brain.contracts import BrainError, paragraph
from team_brain.host import open_brain, protected
from tests.fakes import FakeFeishu, PRINCIPALS, member_principals
from tests.test_lifecycle import material


@unittest.skipUnless(os.name == "posix", "production host permission checks require POSIX")
class HostSecurity(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="team-brain-test-", dir=Path.home())
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config.json"
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config = {"app_id":"synthetic-app", "app_secret":"synthetic-secret", "space_id":"space",
                       "root_node":"root", "wiki_origin":"https://synthetic.feishu.cn",
                       "state_directory":str(self.state), "principals":[p.identity() for p in PRINCIPALS]}
        self.write_config()

    def tearDown(self):
        self.directory.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.config_path.chmod(0o600)

    def test_rejects_exposed_config_symlink_and_writable_parent(self):
        protected(self.config_path)
        self.config_path.chmod(0o644)
        with self.assertRaisesRegex(BrainError, "unsafe_host_permissions"):
            protected(self.config_path)
        self.config_path.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(self.config_path)
        with self.assertRaisesRegex(BrainError, "unsafe_host_path"):
            protected(link)
        self.root.chmod(0o722)
        with self.assertRaisesRegex(BrainError, "unsafe_host_parent"):
            protected(self.config_path)
        self.root.chmod(0o700)

    def test_six_explicit_environments_and_private_database(self):
        brain = open_brain(self.config_path)
        brain.journal.close()
        self.assertEqual((self.state / "team-brain.sqlite3").stat().st_mode & 0o077, 0)
        self.config["principals"] = self.config["principals"][:5]
        self.write_config()
        with self.assertRaisesRegex(BrainError, "two_to_five_members_required"):
            open_brain(self.config_path)
        self.config["principals"] = [PRINCIPALS[0].identity()] * 6
        self.write_config()
        with self.assertRaisesRegex(BrainError, "invalid_environment_mapping"):
            open_brain(self.config_path)

    def test_two_account_mapping_and_protected_subject_policy(self):
        self.config['principals'] = [p.identity() for p in PRINCIPALS[:4]]
        self.config['subject_access'] = [{'principal': p.identity(), 'subjects': ['team', 'management']
                                        if p == PRINCIPALS[0] else ['team']} for p in PRINCIPALS[:4]]
        self.write_config()
        brain = open_brain(self.config_path)
        self.addCleanup(brain.journal.close)
        brain.authorize_subject(PRINCIPALS[0], 'management')
        with self.assertRaisesRegex(BrainError, 'subject_not_accessible'):
            brain.authorize_subject(PRINCIPALS[1], 'management')
        for bad in (None, [], self.config['subject_access'][:-1]):
            self.config['subject_access'] = bad
            self.write_config()
            with self.assertRaisesRegex(BrainError, 'invalid_subject_access'):
                open_brain(self.config_path)

    def test_five_members_on_real_posix_host(self):
        principals = member_principals(5)
        self.config['principals'] = [p.identity() for p in principals]
        self.config['subject_access'] = [{'principal': p.identity(), 'subjects': ['team']} for p in principals]
        self.write_config()
        brain = open_brain(self.config_path)
        brain.journal.close()


class MemberMapping(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config.json'
        self.config = dict(app_id='fixture', app_secret='fixture', space_id='space', root_node='root',
                           wiki_origin='https://synthetic.feishu.cn', state_directory=directory.name)
        # Mapping checks are portable; HostSecurity checks real POSIX permissions.
        protection = patch('team_brain.host.protected', side_effect=lambda p, directory=False: Path(p))
        protection.start()
        self.addCleanup(protection.stop)

    def open(self):
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        brain = open_brain(self.path)
        self.addCleanup(brain.journal.close)
        return brain

    def personal_apps(self, count):
        legacy = member_principals(count)
        principals = [replace(p, user_id='personal-' + p.user_id) if i % 2 == 0 else
                      replace(p, chat_id='shared-group') for i, p in enumerate(legacy)]
        self.config['principals'] = [p.identity() for p in principals]
        self.config['member_bindings'] = {p.key: old.user_id for p, old in zip(principals, legacy)}
        return principals

    def test_two_to_five_members_require_two_unique_entries_each(self):
        for count in (2, 3, 4, 5):
            principals = [p.identity() for p in member_principals(count)]
            self.config['principals'] = principals
            for policy in (None, [{'principal': p, 'subjects': ['team']} for p in principals]):
                self.config.pop('subject_access', None)
                if policy is not None:
                    self.config['subject_access'] = policy
                self.open().journal.close()
        self.config.pop('subject_access', None)
        normal = [p.identity() for p in member_principals(2)]
        bad_mappings = [[], normal[:3], normal * 3, normal[:1] * 4,
                        [dict(p, user_id='member0') if i == 2 else p for i, p in enumerate(normal)],
                        [dict(p, project='duplicate') for p in normal],
                        [dict(p, platform='unknown') for p in normal]]
        for principals in bad_mappings:
            self.config['principals'] = principals
            with self.subTest(principals=principals), self.assertRaises(BrainError):
                self.open()

    def test_cross_app_two_to_five_members_keep_original_authorization(self):
        for count in (2, 3, 4, 5):
            principals = self.personal_apps(count)
            self.config['subject_access'] = [{'principal': p.identity(), 'subjects': ['team', 'management']
                if p == principals[0] else ['team']} for p in principals]
            brain = self.open()
            brain.api = FakeFeishu()
            actor = principals[0]
            brain.authorize_subject(actor, 'management')
            proposal = brain.tool('knowledge_propose', material(subject='team'), actor, 'session')
            callback = replace(actor, message_id='exact-card')
            brain.bind(proposal['approval_id'], callback)
            for other in principals[1:]:
                with self.subTest(count=count, principal=other.project):
                    brain.authorize_subject(other, 'team')
                    with self.assertRaisesRegex(BrainError, 'subject_not_accessible'):
                        brain.authorize_subject(other, 'management')
                    copied_card = replace(other, message_id='exact-card')
                    for call in (lambda: brain.proposal_status(proposal['approval_id'], other),
                                 lambda: brain.bind(proposal['approval_id'], copied_card),
                                 lambda: brain.claim(proposal['approval_id'], 'approve', copied_card),
                                 lambda: brain.execute(proposal['approval_id'], copied_card)):
                        with self.assertRaisesRegex(BrainError, 'principal_mismatch'):
                            call()
            self.assertEqual(brain.api.writes, 0)
            brain.journal.close()

    def test_member_bindings_are_complete_and_do_not_relax_environment_checks(self):
        principals = self.personal_apps(2)
        bindings = self.config.pop('member_bindings')
        with self.assertRaisesRegex(BrainError, 'invalid_environment_mapping'):
            self.open()
        bad_bindings = [None, [], {}, {principals[0].key: 'member0'}, dict(bindings, unknown='member0')]
        bad_bindings += [dict(bindings, **{principals[0].key: value})
                        for value in (None, [], 1, '', ' ', '\x00', 'a' * 257, 'member1')]
        bad_bindings += [{p.key: 'one-member' for p in principals}]
        for bad in bad_bindings:
            self.config['member_bindings'] = bad
            with self.subTest(bindings=bad), self.assertRaises(BrainError):
                self.open()
        for count in (1, 6):
            self.personal_apps(count)
            with self.assertRaisesRegex(BrainError, 'two_to_five_members_required'):
                self.open()
        normal = self.personal_apps(2)
        for invalid in (normal[:-1], normal[:1] * 4,
                        [replace(p, project='duplicate') for p in normal],
                        [replace(p, platform='unknown') for p in normal]):
            self.config['principals'] = [p.identity() for p in invalid]
            self.config['member_bindings'] = {p.key: 'member' + str(i // 2) for i, p in enumerate(invalid)}
            with self.subTest(principals=invalid), self.assertRaises(BrainError):
                self.open()

    def test_duplicate_binding_json_keys_are_rejected(self):
        principals = self.personal_apps(2)
        raw = json.dumps(self.config).replace('"member_bindings": {',
            '"member_bindings": {' + json.dumps(principals[0].key) + ': "member0",', 1)
        self.path.write_text(raw, encoding='utf-8')
        with self.assertRaisesRegex(BrainError, 'invalid_input'):
            open_brain(self.path)

    def test_new_personal_app_does_not_inherit_old_read_receipts_or_proposals(self):
        old = member_principals(2)
        self.config['principals'] = [p.identity() for p in old]
        brain = self.open()
        api = FakeFeishu()
        brain.api = api
        quote = 'FICTIONAL-SOURCE'
        api.pages['source'] = dict(node_token='source', document_id='source', title='source', revision=1,
                                  text=quote, blocks=[paragraph(quote)], url=api.url('source'))
        brain.journal.register('source', 'team')
        brain.tool('knowledge_read', {'subject': 'team', 'node_token': 'source'}, old[0], 'session')
        proposal = brain.tool('knowledge_propose', material(subject='team'), old[0], 'session')
        brain.journal.close()
        current = self.personal_apps(2)
        brain = self.open()
        brain.api = api
        cited = material(quote, subject='team')
        cited['statements'][0]['source'] = {'node_token': 'source'}
        with self.assertRaisesRegex(BrainError, 'source_must_be_read_again'):
            brain.tool('knowledge_propose', cited, current[0], 'session')
        with self.assertRaisesRegex(BrainError, 'principal_mismatch'):
            brain.proposal_status(proposal['approval_id'], current[0])
        with self.assertRaisesRegex(BrainError, 'principal_mismatch'):
            brain.authorize(old[0])
        brain.tool('knowledge_read', {'subject': 'team', 'node_token': 'source'}, current[0], 'session')
        self.assertEqual(brain.tool('knowledge_propose', cited, current[0], 'session')['status'], 'pending')


if __name__ == "__main__":
    unittest.main()
