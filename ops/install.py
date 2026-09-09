"""Explicit, hash-bound VPS cutover. check is read-only; no automatic deployment."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import tempfile
import time
import tomllib

SERVICE = 'cc-connect.service'
GATEWAY = Path('/usr/local/bin/cc-connect-anc')
DROPIN = Path('/etc/systemd/system/cc-connect.service.d/70-team-brain.conf')
CONFIG = Path('/etc/cc-connect/config.toml')
STATE = Path('/var/lib/team-brain')


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(args, timeout=45, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    # Do not forward subprocess output, which may contain configuration or material.
    require(result.returncode == 0, 'command_failed:' + Path(args[0]).name)
    return result.stdout.strip()


def safe_path(path, owners, missing=False):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'invalid_path')
    for item in reversed([path, *path.parents]):
        if not item.exists() and not item.is_symlink():
            require(missing, 'missing_path:' + str(item))
            continue
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid in owners and
                not stat.S_IMODE(info.st_mode) & 0o022, 'unsafe_path:' + str(item))
        require(not set(os.listxattr(item)) & {'system.posix_acl_access', 'system.posix_acl_default'},
                'unexpected_acl:' + str(item))
        require(stat.S_ISDIR(info.st_mode) if item != path else
                stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'unsupported_path')
    return path


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, mode, uid, gid, owners):
    path = safe_path(path, owners, missing=True)
    fd, temporary = tempfile.mkstemp(prefix='.team-brain-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fchown(output.fileno(), uid, gid)
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        safe_path(path, owners, missing=True)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def log_fields(record):
    message = record.get('MESSAGE')
    if not isinstance(message, str):
        return {}
    try:
        fields = dict(part.split('=', 1) for part in shlex.split(message) if '=' in part)
        # This event is emitted before the gateway installs its logfmt handler.
        if re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} INFO config loaded path=\S+', message):
            fields['msg'] = 'config loaded'
        return fields
    except ValueError:
        raise RuntimeError('unparseable_receiver_log') from None


def engine_ready(records, projects):
    events = [log_fields(record) for record in records]
    started = [event.get('project') for event in events if event.get('msg') == 'engine started']
    return (len(started) == len(projects) and set(started) == set(projects)
            and not any(event.get('msg') == 'engine started with partial readiness' for event in events))


def activity(records, now, reviewed=()):
    require(records, 'missing_receiver_journal')
    received, completed, processing, last = {}, {}, set(), 0
    process_boundaries = {}
    for record in records:
        fields = log_fields(record)
        boundaries = process_boundaries.setdefault(record.get('_PID', ''), 0)
        if fields.get('msg') == 'config loaded' and fields.get('path') == str(CONFIG):
            process_boundaries[record.get('_PID', '')] = boundaries + 1
        event, message_id = fields.get('msg'), fields.get('msg_id')
        stamp = int(record['__REALTIME_TIMESTAMP'])
        if event in ('message received', 'turn complete'):
            require(message_id, 'missing_message_binding')
            (received if event == 'message received' else completed)[message_id] = stamp
        if message_id and event not in ('message received', 'turn complete'):
            processing.add(message_id)
        if event in ('message received', 'processing message', 'message queued for busy session',
                     'processing queued message', 'turn complete'):
            last = max(last, stamp)
    # Old gateway versions omit terminal events for synchronous commands. Only an
    # explicitly reviewed, exact historical event can be exempted; never a turn.
    exceptions = {(r['message_id'], r['received_at']) for r in reviewed if r.get('evidence')}
    unresolved = [key for key, stamp in received.items() if completed.get(key, 0) <= stamp
                  and not (key not in processing and (key, stamp) in exceptions)]
    require(not unresolved, 'unfinished_message_requires_event_review')
    # config loaded precedes all engine creation/ingress. Require that boundary
    # for each PID; partial startup can safely roll back, rotated logs cannot.
    require(all(count == 1 for count in process_boundaries.values()), 'incomplete_receiver_journal')
    require(now - last / 1e6 >= 120, 'receiver_not_quiet')
    return {'last_activity': last, 'received': len(received), 'completed': len(completed)}


def crm_idle(paths):
    for path in paths:
        with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            require(db.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'crm_integrity')
            for table in ('staged_changes', 'approvals'):
                states = {r[0] for r in db.execute('SELECT DISTINCT state FROM ' + table)}
                require(states <= {'verified', 'cancelled', 'superseded', 'staged', 'pending', 'expired', 'failed'},
                        'unresolved_crm_operation')
            now = datetime.datetime.now(datetime.timezone.utc)
            for row in db.execute("SELECT expires_at FROM approvals WHERE state='pending'"):
                require(datetime.datetime.fromisoformat(row[0].replace('Z', '+00:00')) <= now,
                        'effective_pending_crm_approval')
            require(db.execute('SELECT count(*) FROM write_fences').fetchone()[0] == 0, 'crm_write_fence')


class Release:
    def __init__(self, bundle):
        import pwd
        self.bundle = safe_path(Path(bundle).absolute(), {0})
        require(stat.S_IMODE(self.bundle.stat().st_mode) == 0o700, 'bundle_must_be_private')
        self.plan_data = safe_path(self.bundle / 'plan.json', {0}).read_bytes()
        self.plan = json.loads(self.plan_data)
        self.host = pwd.getpwnam('ccconnect')
        self.owners = {0, self.host.pw_uid}
        self.agent_roots = {}
        self.config = tomllib.loads(safe_path(CONFIG, {0}).read_text())
        self.projects = self.config['projects']
        require(len(self.projects) == 6, 'six_projects_required')
        for project in self.projects:
            account = pwd.getpwnam(project['run_as_user'])
            require(account.pw_uid not in (0, self.host.pw_uid), 'agent_not_isolated')
            workspace = project['agent']['options']['work_dir']
            require(workspace.startswith('/home/' + account.pw_name + '/'), 'unexpected_agent_workspace')
            self.agent_roots[Path('/home') / account.pw_name] = account.pw_uid
        self.receipt_path = self.bundle / 'receipt.json'
        self.prefix = self.plan['release_directory']
        require(re.fullmatch(r'/opt/team-brain/releases/[0-9a-f]{40}', self.prefix), 'invalid_release_directory')
        allowed = {str(GATEWAY), str(DROPIN), '/usr/local/libexec/team-brain-host', '/usr/local/bin/team-brain-tool'}
        for project in self.projects:
            workspace = project['agent']['options']['work_dir']
            allowed.update((workspace + '/.claude/skills/team-brain/SKILL.md', workspace + '/.claude/docs/tools.md'))
        for item in self.plan['files']:
            target = item['target']
            is_code = target.startswith(self.prefix + '/') and target.endswith('.py')
            require(target in allowed or is_code, 'unapproved_install_target')
            source = Path(item['source'])
            require(not source.is_absolute() and '..' not in source.parts, 'invalid_source')
            require(sha(safe_path(self.bundle / source, {0}).read_bytes()) == item['sha256'], 'payload_changed')
        require(len({f['target'] for f in self.plan['files']}) == len(self.plan['files']), 'duplicate_target')
        require(allowed <= {f['target'] for f in self.plan['files']}, 'missing_install_target')
        require(str(CONFIG) in self.plan['preserve'], 'missing_config_baseline')

    def path_owners(self, path):
        path = Path(path)
        for root, uid in self.agent_roots.items():
            if path == root or root in path.parents:
                return {0, uid}
        return self.owners

    def record(self, value):
        atomic_write(self.receipt_path, json.dumps(value, indent=2).encode(), 0o600, 0, 0, {0})

    def receipt(self):
        receipt = json.loads(safe_path(self.receipt_path, {0}).read_text())
        require(receipt['plan_sha256'] == sha(self.plan_data), 'receipt_plan_mismatch')
        return receipt

    def journal(self, pid):
        return [json.loads(line) for line in run(['journalctl', '-u', SERVICE, '_PID=' + str(pid),
                '-o', 'json', '--no-pager']).splitlines() if line]

    def startup_journal(self, receipt):
        started = datetime.datetime.fromisoformat(receipt['start_requested_at'])
        require(started.tzinfo is not None, 'startup_time_requires_timezone')
        # journalctl on the deployed distro rejects ISO timestamps with +00:00.
        # Round down, never forward, so even the first process remains in scope.
        since = started.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        return [record for line in run(['journalctl', '-u', SERVICE, '--since', since,
                '-o', 'json', '--no-pager']).splitlines() if line
                for record in [json.loads(line)] if record.get('_COMM') == 'cc-connect-anc']

    def preserved(self):
        for path, digest in self.plan['preserve'].items():
            require(sha(safe_path(path, self.path_owners(path)).read_bytes()) == digest, 'preserved_file_changed:' + path)

    def stopped(self):
        require(run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value']) == '0', 'service_must_be_stopped')
        require(run(['systemctl', 'show', SERVICE, '-p', 'ActiveState', '--value']) in ('inactive', 'failed'), 'service_not_stopped')
        require(not self.receivers(), 'receiver_process_remains')
        require(not any(':18743 ' in line for line in run(['ss', '-H', '-ltnp']).splitlines()), 'listener_remains')

    def receivers(self):
        return [line.split()[0] for line in run(['ps', '-eo', 'pid=,comm=']).splitlines()
                if line.split()[-1] == 'cc-connect-anc']

    def check(self):
        self.preserved()
        require(run(['systemctl', 'is-active', SERVICE]) == 'active', 'receiver_not_active')
        require(run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value']) == self.plan['old_pid'], 'pid_changed')
        require(run(['systemctl', 'show', SERVICE, '-p', 'User', '--value']) == 'ccconnect', 'host_user_changed')
        require(run(['systemctl', 'show', SERVICE, '-p', 'KillMode', '--value']) == 'control-group', 'kill_mode_changed')
        require(self.receivers() == [self.plan['old_pid']], 'receiver_count_mismatch')
        require(Path('/proc/' + self.plan['old_pid']).stat().st_uid == self.host.pw_uid, 'receiver_uid_changed')
        require(sha(safe_path(GATEWAY, {0}).read_bytes()) == self.plan['old_gateway_sha256'], 'gateway_changed')
        for item in self.plan['files']:
            path = safe_path(item['target'], self.path_owners(item['target']), missing=True)
            require(path == GATEWAY or not path.exists(), 'new_file_already_exists:' + str(path))
        require(not STATE.exists() and not STATE.is_symlink(), 'knowledge_state_already_exists')
        crm_idle(self.plan['crm_ledgers'])
        return activity(self.journal(self.plan['old_pid']), time.time(), self.plan.get('completed_without_turn', ()))

    def prepare(self):
        require(not self.receipt_path.exists(), 'existing_release_receipt')
        baseline = self.check()
        old = GATEWAY.stat()
        backup = self.bundle / 'gateway.before'
        require(not backup.exists(), 'existing_backup')
        atomic_write(backup, GATEWAY.read_bytes(), 0o600, 0, 0, {0})
        self.record({'plan_sha256': sha(self.plan_data), 'phase': 'prepared', 'activity': baseline,
                     'old_metadata': [stat.S_IMODE(old.st_mode), old.st_uid, old.st_gid]})
        require(self.check() == baseline, 'activity_changed_during_backup')
        return {'phase': 'prepared', 'service_stopped': False}

    def directory(self, path, uid=0, gid=0, mode=0o755):
        path = safe_path(path, self.path_owners(path), missing=True)
        if path.exists():
            require(path.is_dir(), 'directory_expected')
            return
        self.directory(path.parent)
        path.mkdir(mode=mode)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
        sync_directory(path.parent)

    def host_config(self):
        config = dict(self.plan['host_config'])
        require('app_secret' not in config, 'secret_must_not_be_packaged')
        principals, secrets = [], set()
        for project in self.projects:
            require(len(project['platforms']) == 1, 'ambiguous_platform')
            platform = project['platforms'][0]
            options = platform['options']
            require(platform['type'] == 'feishu' and options['app_id'] == config['app_id'], 'app_mismatch')
            require(not options.get('thread_isolation') and not options.get('share_session_in_channel'), 'dynamic_session_mapping')
            user, chat = options['allow_from'], options['allow_chat']
            principals.append(dict(platform='feishu', user_id=user, chat_id=chat,
                              session_key='feishu:' + chat + ':' + user, project=project['name']))
            secrets.add(options['app_secret'])
        require(principals == config['principals'] and len(secrets) == 1, 'identity_mapping_changed')
        require(config['state_directory'] == str(STATE / 'state'), 'wrong_state_directory')
        config['app_secret'] = next(iter(secrets))
        return config

    def install(self):
        receipt = self.receipt()
        require(receipt['phase'] == 'prepared', 'install_not_prepared_or_already_started')
        self.stopped()
        require(activity(self.journal(self.plan['old_pid']), time.time(), self.plan.get('completed_without_turn', ())) == receipt['activity'], 'message_raced_with_stop')
        self.preserved()
        crm_idle(self.plan['crm_ledgers'])
        require(sha(GATEWAY.read_bytes()) == self.plan['old_gateway_sha256'], 'gateway_changed')
        for item in self.plan['files']:
            require(Path(item['target']) == GATEWAY or not Path(item['target']).exists(), 'target_changed')
        require(not STATE.exists(), 'knowledge_state_already_exists')
        config = self.host_config()
        receipt['phase'] = 'installing'
        self.record(receipt)  # A partial install is never automatically replayed.
        self.directory(STATE, self.host.pw_uid, self.host.pw_gid, 0o700)
        self.directory(STATE / 'state', self.host.pw_uid, self.host.pw_gid, 0o700)
        for name, value in (('config.json', config), ('seeds.json', self.plan['seeds'])):
            atomic_write(STATE / name, json.dumps(value).encode(), 0o600, self.host.pw_uid, self.host.pw_gid, self.owners)
        for item in self.plan['files']:
            self.directory(Path(item['target']).parent)
            atomic_write(item['target'], (self.bundle / item['source']).read_bytes(), int(item['mode'], 8),
                         0, self.host.pw_gid if item.get('host_group') else 0, self.path_owners(item['target']))
        bootstrap = ('import sys;sys.path.insert(0,' + repr(self.prefix) + ');from ops.bootstrap import main;main()')
        run(['/usr/sbin/runuser', '-u', 'ccconnect', '--', '/usr/bin/python3', '-I', '-B', '-c', bootstrap,
             '--config', str(STATE / 'config.json'), '--seeds', str(STATE / 'seeds.json')],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=180)
        self.verify_files()
        receipt['phase'] = 'installed'
        self.record(receipt)
        return {'phase': 'installed', 'service_started': False, 'seeds': 8}

    def verify_files(self):
        self.preserved()
        for item in self.plan['files']:
            path = safe_path(item['target'], self.path_owners(item['target']))
            info = path.stat()
            require(sha(path.read_bytes()) == item['sha256'] and info.st_uid == 0 and
                    info.st_gid == (self.host.pw_gid if item.get('host_group') else 0) and
                    stat.S_IMODE(info.st_mode) == int(item['mode'], 8), 'installed_file_mismatch')
        for project in self.projects:
            code = 'import os;assert not any(os.access(p,os.R_OK) for p in '+repr([
                str(STATE/'config.json'), str(STATE/'state'), str(CONFIG)])+');assert not os.access("/usr/local/libexec/team-brain-host",os.X_OK)'
            run(['/usr/sbin/runuser', '-u', project['run_as_user'], '--', '/usr/bin/python3', '-I', '-c', code])
            result = run(['/usr/sbin/runuser', '-u', project['run_as_user'], '--', '/usr/local/bin/team-brain-tool', 'knowledge_search'],
                         input='{}', env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            require(json.loads(result) == {'status': 'unavailable', 'code': 'no_host_session'}, 'unprivileged_tool_smoke_failed')

    def start(self):
        receipt = self.receipt()
        require(receipt['phase'] == 'installed', 'start_not_ready')
        self.stopped()
        self.verify_files()
        receipt['phase'] = 'starting'
        receipt['start_requested_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.record(receipt)
        try:
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'start', SERVICE])
            pid = run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value'])
            require(pid.isdigit() and pid != '0', 'missing_started_pid')
            receipt['new_pid'] = pid
            self.record(receipt)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                records = self.journal(pid)
                if engine_ready(records, [p['name'] for p in self.projects]) and sum(
                        'connected to wss' in str(r.get('MESSAGE', '')) for r in records) == 1:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('startup_health_timeout')
            require(run(['systemctl', 'is-active', SERVICE]) == 'active', 'not_active')
            require(self.receivers() == [pid], 'receiver_count_mismatch')
            require(run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value']) == pid, 'unexpected_receiver_restart')
            listeners = [line for line in run(['ss', '-H', '-ltnp']).splitlines() if ':18743 ' in line]
            require(len(listeners) == 1 and '127.0.0.1:18743' in listeners[0] and 'pid='+pid+',' in listeners[0], 'listener_mismatch')
            receipt['phase'] = 'started_pending_acceptance'
            self.record(receipt)
            return {'phase': receipt['phase'], 'pid': pid}
        except BaseException:
            # A connected receiver may already be handling business. Health
            # failure is not permission to kill that work or replay a publish.
            receipt['phase'] = 'startup_failed_needs_inspection'
            self.record(receipt)
            raise

    def rollback(self):
        receipt = self.receipt()
        require(receipt['phase'] in ('installing', 'installed', 'starting',
                'startup_failed_needs_inspection', 'started_pending_acceptance'), 'nothing_to_restore')
        self.stopped()
        crm_idle(self.plan['crm_ledgers'])
        if receipt.get('start_requested_at'):
            # Include every receiver PID since the attempt, including restarts or
            # a process that started before its PID could be saved in the receipt.
            activity(self.startup_journal(receipt), time.time())
        # Unknown operations and even partially initialized journals are retained
        # verbatim. Restoring the old receiver disables knowledge execution.
        self.preserved()
        backup = safe_path(self.bundle / 'gateway.before', {0}).read_bytes()
        require(sha(backup) == self.plan['old_gateway_sha256'], 'backup_changed')
        if DROPIN.exists() or DROPIN.is_symlink():
            expected = next(f['sha256'] for f in self.plan['files'] if f['target'] == str(DROPIN))
            require(sha(safe_path(DROPIN, {0}).read_bytes()) == expected, 'dropin_changed')
        mode, uid, gid = receipt['old_metadata']
        atomic_write(GATEWAY, backup, mode, uid, gid, {0})
        if DROPIN.exists():
            DROPIN.unlink()
            sync_directory(DROPIN.parent)
        run(['systemctl', 'daemon-reload'])
        receipt['phase'] = 'restored_stopped'
        self.record(receipt)
        return {'phase': receipt['phase'], 'knowledge_data_preserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('check', 'prepare', 'install', 'start', 'rollback'))
    parser.add_argument('bundle')
    parser.add_argument('--approved-plan-sha256')
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'root_posix_operator_required')
    os.umask(0o077)
    release = Release(args.bundle)
    if args.operation != 'check':
        require(args.approved_plan_sha256 == sha(release.plan_data), 'explicit_frozen_plan_approval_required')
        import fcntl
        lock_path = safe_path('/run/team-brain-deploy.lock', {0}, missing=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+') as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                    stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_deployment_lock')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = getattr(release, args.operation)()
    else:
        result = release.check()
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No traceback/config/remote response is printed by the production entry point.
        print(json.dumps({'status': 'blocked', 'reason': str(error) if isinstance(error, RuntimeError) else type(error).__name__}))
        raise SystemExit(1)
