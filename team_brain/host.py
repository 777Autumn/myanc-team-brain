"""Private subprocess entry point. Install only in the trusted host environment."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from .contracts import BrainError, Principal, fields, require, strict_json, text, token
from .feishu import Feishu
from .journal import Journal
from .service import Brain, bodyprint


def protected(path, directory=False):
    require(os.name == "posix", "production_host_requires_posix_permissions")
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(), "unsafe_host_path")
    info = path.stat()
    require(info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o077,
            "unsafe_host_permissions")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "unsafe_host_path")
    # Ancestors must not be writable by an untrusted account, including /tmp.
    for ancestor in path.parents:
        info = ancestor.stat()
        require(not ancestor.is_symlink() and info.st_uid in (0, os.geteuid()) and
                not stat.S_IMODE(info.st_mode) & 0o022, "unsafe_host_parent")
    return path


def open_brain(config_path):
    config = strict_json(protected(config_path).read_text(encoding="utf-8"))
    fields(config, ("app_id", "app_secret", "space_id", "root_node", "wiki_origin", "state_directory", "principals"),
           ("subject_access",))
    require("subject_access" not in config or config["subject_access"] is not None, "invalid_subject_access")
    state = protected(config["state_directory"], True)
    require(isinstance(config["principals"], list) and len(config["principals"]) in (4, 6),
            "four_or_six_environments_required")
    principals = [Principal.parse(p) for p in config["principals"]]
    count = len(principals)
    require(len({p.key for p in principals}) == count and len({p.user_id for p in principals}) == count // 2 and
            len({p.project for p in principals}) == count and all(p.platform == "feishu" for p in principals),
            "invalid_environment_mapping")
    os.umask(0o077)
    database = state / "team-brain.sqlite3"
    if database.exists():
        protected(database)
    journal = Journal(database)
    try:
        return Brain(journal, Feishu(config["app_id"], config["app_secret"], config["space_id"],
                                   config["root_node"], config["wiki_origin"]), principals,
                     subject_access=config.get("subject_access"))
    except Exception:
        journal.close()
        raise


def dispatch(brain, envelope):
    fields(envelope, ("operation", "principal"), ("command", "input", "session_token", "approval_id", "decision"))
    principal = Principal.parse(envelope["principal"])
    operation = envelope["operation"]
    common = ("operation", "principal")
    if operation == "tool":
        fields(envelope, common + ("command", "input", "session_token"))
        return brain.tool(envelope["command"], envelope["input"], principal, envelope["session_token"])
    if operation == "bind":
        fields(envelope, common + ("approval_id",))
        return brain.bind(envelope["approval_id"], principal)
    if operation == "claim":
        fields(envelope, common + ("approval_id", "decision"))
        return brain.claim(envelope["approval_id"], envelope["decision"], principal)
    require(operation == "execute", "unsupported_operation")
    fields(envelope, common + ("approval_id",))
    return brain.execute(envelope["approval_id"], principal)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--admin", choices=("register", "pause", "resume", "reconcile", "inspect"))
    parser.add_argument("--id")
    parser.add_argument("--subject")
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    brain = None
    try:
        brain = open_brain(args.config)
        if args.admin:
            item = token(args.id)
            if args.admin == "register":
                require(args.reviewed, "owner_review_required")
                snapshot = brain.api.snapshot(item)
                with brain.journal.transaction():
                    require(not brain.journal.locked(item), "page_busy")
                    brain.journal.register(item, text(args.subject, 100))
                    brain.journal.baseline(item, bodyprint(snapshot))
                result = {"status": "registered", "url": snapshot["url"]}
            elif args.admin in ("pause", "resume"):
                if args.admin == "resume":
                    require(args.reviewed, "owner_review_required")
                    snapshot = brain.api.snapshot(item)
                    with brain.journal.transaction():
                        brain.journal.page(item)
                        require(not brain.journal.locked(item), "page_busy")
                        brain.journal.baseline(item, bodyprint(snapshot))
                        brain.journal.db.execute("UPDATE pages SET paused=0 WHERE node=?", (item,))
                else:
                    brain.journal.pause(item, True)
                result = {"status": args.admin}
            elif args.admin == "reconcile":
                result = brain.reconcile(item)
            else:
                result = brain.journal.get(item)
        else:
            raw = sys.stdin.read(65537)
            require(len(raw.encode()) <= 65536, "input_too_large")
            result = dispatch(brain, strict_json(raw))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        code = exc.code if isinstance(exc, BrainError) else "host_failure"
        print(json.dumps({"status": "blocked", "code": code}))
    finally:
        if brain:
            brain.journal.close()


if __name__ == "__main__":
    main()
