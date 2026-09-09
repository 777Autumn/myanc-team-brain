import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops import install


def log(event, second, message=''):
    return {'MESSAGE': 'msg=' + json.dumps(event) + (' msg_id=' + message if message else ''),
            '__REALTIME_TIMESTAMP': str(second * 1000000)}


def boundary(pid=''):
    return dict(log('config loaded', 1), MESSAGE='msg="config loaded" path=' + json.dumps(str(install.CONFIG)), _PID=pid)


class Quiescence(unittest.TestCase):
    def test_completed_turn_must_also_be_quiet(self):
        records = [boundary(), log('message received', 10, 'one'), log('turn complete', 20, 'one')]
        self.assertEqual(install.activity(records, 141)['received'], 1)
        with self.assertRaisesRegex(RuntimeError, 'receiver_not_quiet'):
            install.activity(records, 139)

    def test_command_exception_requires_exact_reviewed_event(self):
        records = [boundary(), log('message received', 10, 'help')]
        evidence = [dict(message_id='help', received_at=10000000, evidence='Reviewed synchronous reply')]
        with self.assertRaisesRegex(RuntimeError, 'event_review'):
            install.activity(records, 200)
        self.assertEqual(install.activity(records, 200, evidence)['received'], 1)
        with self.assertRaisesRegex(RuntimeError, 'event_review'):
            install.activity([log('message received', 11, 'help')], 200, evidence)
        with self.assertRaisesRegex(RuntimeError, 'event_review'):
            install.activity(records + [log('user message accepted for processing', 11, 'help')], 200, evidence)

    def test_missing_journal_is_not_idle(self):
        for records in ([], [log('unrelated', 10)]):
            with self.assertRaises(RuntimeError):
                install.activity(records, 200)
        self.assertEqual(install.activity([boundary()], 200)['received'], 0)

    def test_completed_pair_without_startup_boundary_is_incomplete(self):
        with self.assertRaisesRegex(RuntimeError, 'incomplete_receiver_journal'):
            install.activity([log('message received', 10, 'one'), log('turn complete', 20, 'one')], 200)

    def test_partial_or_duplicate_engine_is_not_six_ready_projects(self):
        projects = ['fictional-' + str(n) for n in range(6)]
        records = [dict(log('engine started', 1), MESSAGE='msg="engine started" project=' + p) for p in projects]
        self.assertTrue(install.engine_ready(records, projects))
        records[-1]['MESSAGE'] = 'msg="engine started with partial readiness" project=' + projects[-1]
        self.assertFalse(install.engine_ready(records, projects))
        with self.assertRaisesRegex(RuntimeError, 'incomplete_receiver_journal'):
            install.activity(records[:-1], 200)
        self.assertFalse(install.engine_ready(records[:5] + [records[0]], projects))

    def test_restarts_and_partial_startup_can_be_idle_but_each_pid_needs_boundary(self):
        records = [boundary('111'), boundary('222')]
        for pid in ('111', '222'):
            records.extend(dict(log('engine started', 2), _PID=pid) for _ in range(6))
        records.append(dict(log('engine started with partial readiness', 3), _PID='222'))
        self.assertEqual(install.activity(records, 200)['received'], 0)
        with self.assertRaisesRegex(RuntimeError, 'incomplete_receiver_journal'):
            install.activity(records[1:], 200)

    def test_default_go_logger_startup_boundary_before_logfmt(self):
        record = dict(boundary(), MESSAGE='2026/01/01 00:00:00 INFO config loaded path=/etc/cc-connect/config.toml')
        with patch.object(install, 'CONFIG', '/etc/cc-connect/config.toml'):
            self.assertEqual(install.activity([record], 200)['received'], 0)
            record['MESSAGE'] = 'untrusted text: ' + record['MESSAGE']
            with self.assertRaisesRegex(RuntimeError, 'incomplete_receiver_journal'):
                install.activity([record], 200)


class ReleaseGates(unittest.TestCase):
    def release(self):
        release = install.Release.__new__(install.Release)
        release.owners = {0, 900}
        release.agent_roots = {Path('/home/fictional-agent'): 1000}
        release.host = SimpleNamespace(pw_uid=900, pw_gid=900)
        release.plan = {'crm_ledgers': [], 'old_pid': '123'}
        release.projects = []
        release.record = Mock()
        release.stopped = Mock()
        release.preserved = Mock()
        return release

    def test_agent_uid_is_only_allowed_under_own_home(self):
        release = self.release()
        self.assertEqual(release.path_owners('/opt/team-brain/source.py'), {0, 900})
        self.assertEqual(release.path_owners('/var/lib/team-brain/config.json'), {0, 900})
        self.assertEqual(release.path_owners('/home/fictional-agent/work/file'), {0, 1000})
        self.assertEqual(release.path_owners('/home/fictional-agent-other/work/file'), {0, 900})

    def test_host_ancestor_owned_by_agent_is_rejected(self):
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'is_symlink', return_value=False), \
             patch.object(Path, 'lstat', side_effect=lambda: SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)):
            with self.assertRaisesRegex(RuntimeError, 'unsafe_path'):
                install.safe_path(Path('/opt/team-brain/source.py').absolute(), {0, 900})

    def test_partial_install_cannot_be_replayed(self):
        release = self.release()
        release.receipt = lambda: {'phase': 'installing'}
        with self.assertRaisesRegex(RuntimeError, 'already_started'):
            release.install()
        release.stopped.assert_not_called()
        release.record.assert_not_called()

    def test_message_after_prepare_blocks_all_install_writes(self):
        release = self.release()
        release.receipt = lambda: {'phase': 'prepared', 'activity': {'last_activity': 0}}
        release.journal = lambda _: [log('message received', 10, 'new')]
        with self.assertRaisesRegex(RuntimeError, 'event_review'):
            release.install()
        release.record.assert_not_called()

    def test_start_failure_preserves_possibly_busy_receiver(self):
        release = self.release()
        release.receipt = lambda: {'phase': 'installed'}
        release.verify_files = Mock()
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ['systemctl', 'start']:
                raise RuntimeError('uncertain start result')
            return ''
        with patch.object(install, 'run', side_effect=command):
            with self.assertRaisesRegex(RuntimeError, 'uncertain start'):
                release.start()
        self.assertFalse(any(args[:2] == ['systemctl', 'stop'] for args in calls))
        self.assertEqual(release.record.call_args.args[0]['phase'], 'startup_failed_needs_inspection')

    def test_rollback_preserves_unknown_and_partial_knowledge_state(self):
        release = self.release()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gateway, dropin, database = root/'gateway', root/'dropin', root/'state/state/team-brain.sqlite3'
            database.parent.mkdir(parents=True)
            database.write_bytes(b'partial journal, retain every byte')
            gateway.write_bytes(b'new gateway')
            dropin.write_bytes(b'knowledge entry')
            (root/'gateway.before').write_bytes(b'old gateway')
            release.bundle = root
            release.plan.update(old_gateway_sha256=install.sha(b'old gateway'), files=[
                dict(target=str(dropin), sha256=install.sha(b'knowledge entry'))])
            release.receipt = lambda: dict(phase='installed', old_metadata=[0o755, 0, 0])
            def write(path, data, *args):
                Path(path).write_bytes(data)
            with patch.multiple(install, GATEWAY=gateway, DROPIN=dropin, STATE=root/'state'), \
                 patch.object(install, 'safe_path', side_effect=lambda path, *args: Path(path)), \
                 patch.object(install, 'atomic_write', side_effect=write), \
                 patch.object(install, 'sync_directory'), patch.object(install, 'run'):
                release.rollback()
            self.assertEqual(gateway.read_bytes(), b'old gateway')
            self.assertFalse(dropin.exists())
            self.assertEqual(database.read_bytes(), b'partial journal, retain every byte')

    def test_stop_gate_requires_no_orphan_receiver(self):
        release = self.release()
        release.receivers = lambda: ['456']
        with patch.object(install, 'run', side_effect=['0', 'inactive']):
            with self.assertRaisesRegex(RuntimeError, 'receiver_process_remains'):
                install.Release.stopped(release)
