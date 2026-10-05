import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from team_brain.contracts import BrainError
from team_brain.host import open_brain, protected
from tests.fakes import PRINCIPALS, member_principals


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
    def test_two_to_five_members_require_two_unique_entries_each(self):
        # Only mapping logic is portable. HostSecurity separately checks real
        # POSIX ownership/permissions; this patch is not permission evidence.
        with tempfile.TemporaryDirectory() as root, patch('team_brain.host.protected', side_effect=lambda p, directory=False: Path(p)):
            path = Path(root) / 'config.json'
            config = dict(app_id='fixture', app_secret='fixture', space_id='space', root_node='root',
                          wiki_origin='https://synthetic.feishu.cn', state_directory=root)
            for count in (2, 3, 4, 5):
                principals = [p.identity() for p in member_principals(count)]
                config.update(principals=principals)
                for policy in (None, [{'principal': p, 'subjects': ['team']} for p in principals]):
                    config.pop('subject_access', None)
                    if policy is not None:
                        config['subject_access'] = policy
                    path.write_text(json.dumps(config), encoding='utf-8')
                    brain = open_brain(path)
                    brain.journal.close()
            config.pop('subject_access', None)
            normal = [p.identity() for p in member_principals(2)]
            bad_mappings = [[], normal[:3], normal * 3, normal[:1] * 4,
                            [dict(p, user_id='member0') if i == 2 else p for i, p in enumerate(normal)],
                            [dict(p, project='duplicate') for p in normal],
                            [dict(p, platform='unknown') for p in normal]]
            for principals in bad_mappings:
                config['principals'] = principals
                path.write_text(json.dumps(config), encoding='utf-8')
                with self.subTest(principals=principals), self.assertRaises(BrainError):
                    open_brain(path)


if __name__ == "__main__":
    unittest.main()
