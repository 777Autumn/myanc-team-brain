"""Unprivileged model tool. No credentials, identity arguments or publish action."""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from .contracts import BrainError, fields, require, strict_json
from .feishu import NoRedirect


def sandbox_opener():
    # Match the existing gateway client transport: sandbox loopback is reached
    # through Claude's local HTTP proxy, not a direct network connection.
    proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY", "")
    try:
        parsed = urllib.parse.urlsplit(proxy)
        valid = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                 and parsed.port
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                 and all(32 < ord(c) < 127 for c in proxy))
    except ValueError:
        valid = False
    require(valid, "sandbox_proxy_required")
    # Native proxies may authenticate locally. ProxyHandler carries their
    # userinfo as Proxy-Authorization; never expose or log the proxy URL.
    # The native shell excludes localhost by default. Remove bypass only in
    # this short-lived client; the parent sandbox and its policy stay intact.
    os.environ.pop("NO_PROXY", None)
    os.environ.pop("no_proxy", None)
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy}), NoRedirect())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("knowledge_search", "knowledge_read", "knowledge_propose"))
    args = parser.parse_args()
    try:
        raw = sys.stdin.read(60001)
        require(len(raw.encode()) <= 60000, "input_too_large")
        value = strict_json(raw)
        require(isinstance(value, dict))
        session = os.environ.get("TEAM_BRAIN_SESSION_TOKEN", "")
        # The paired gateway gives both domains the same opaque action session.
        # Existing isolated spawns preserve only its historical CRM-named alias.
        # This is a session binding, not a CRM host credential or approval.
        shared_session = os.environ.get("MYANC_CRM_ACTION_TOKEN", "")
        require(not (session and shared_session and session != shared_session), "conflicting_host_session")
        session = session or shared_session
        require(session, "no_host_session")
        # Fixed gateway endpoint; only the validated native proxy can carry it.
        request = urllib.request.Request("http://127.0.0.1:18743/tool", method="POST",
                  headers={"Authorization": "Bearer " + session, "Content-Type": "application/json"},
                  data=json.dumps({"command": args.command, "input": value}).encode())
        opener = sandbox_opener()
        with opener.open(request, timeout=35) as response:
            result = response.read(2_000_001)
            require(len(result) <= 2_000_000, "response_too_large")
            print(json.dumps(strict_json(result), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "unavailable", "code": exc.code if isinstance(exc, BrainError) else "host_unavailable"}))


if __name__ == "__main__":
    main()
