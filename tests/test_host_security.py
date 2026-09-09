import json
import os
from pathlib import Path
import tempfile
import unittest

from team_brain.contracts import BrainError
from team_brain.host import open_brain, protected
from tests.fakes import PRINCIPALS


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
        with self.assertRaisesRegex(BrainError, "six_environments_required"):
            open_brain(self.config_path)
        self.config["principals"] = [PRINCIPALS[0].identity()] * 6
        self.write_config()
        with self.assertRaisesRegex(BrainError, "invalid_environment_mapping"):
            open_brain(self.config_path)


if __name__ == "__main__":
    unittest.main()
