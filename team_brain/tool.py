"""Unprivileged model tool. No credentials, identity arguments or publish action."""

import argparse
import json
import os
import sys
import urllib.request

from .contracts import BrainError, fields, require, strict_json
from .feishu import NoRedirect


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
        require(session, "no_host_session")
        # Fixed loopback endpoint, no proxy or redirects carrying the session token.
        request = urllib.request.Request("http://127.0.0.1:18743/tool", method="POST",
                  headers={"Authorization": "Bearer " + session, "Content-Type": "application/json"},
                  data=json.dumps({"command": args.command, "input": value}).encode())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=35) as response:
            result = response.read(2_000_001)
            require(len(result) <= 2_000_000, "response_too_large")
            print(json.dumps(strict_json(result), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "unavailable", "code": exc.code if isinstance(exc, BrainError) else "host_unavailable"}))


if __name__ == "__main__":
    main()
