"""Host-owned knowledge lifecycle; the model can only search, read and propose."""

import difflib
import re
import time
import urllib.parse
import uuid

from .contracts import BrainError, EVIDENCE, block_text, digest, fields, paragraphs, require, text, token


def fingerprint(snapshot):
    return digest({key: snapshot[key] for key in ("node_token", "title", "revision", "blocks", "text")})


def bodyprint(snapshot):
    return digest({key: snapshot[key] for key in ("title", "blocks", "text")})


class Brain:
    def __init__(self, journal, api, allowed_principals, clock=time.time):
        self.journal, self.api, self.clock = journal, api, clock
        self.allowed = {p.key for p in allowed_principals}

    def authorize(self, principal):
        require(principal.key in self.allowed, "principal_mismatch")

    def owner(self, principal, session_token):
        self.authorize(principal)
        text(session_token, 1024)
        return digest([principal.key, session_token])

    def readable(self, node, subject):
        page = self.journal.page(token(node))
        require(page["subject"] in (subject, "team"), "subject_mismatch")
        require(not self.journal.locked(node), "page_busy")
        snapshot = self.api.snapshot(node)
        self.journal.observe(node, bodyprint(snapshot))
        return snapshot

    def resolve(self, reference):
        text(reference, 400)
        if not reference.startswith("https://"):
            return token(reference)
        parsed = urllib.parse.urlsplit(reference)
        origin = urllib.parse.urlsplit(self.api.wiki_origin)
        require(parsed.netloc == origin.netloc and not parsed.fragment and not parsed.query, "outside_team_scope")
        parts = parsed.path.strip("/").split("/")
        require(len(parts) == 2 and parts[0] in ("wiki", "docx"), "unsupported_reference")
        if parts[0] == "wiki":
            return token(parts[1])
        # Object-token resolution is delegated to Wiki; scope and registry are
        # still enforced by readable() before any document body is returned.
        return token(self.api.node(token(parts[1]), "docx")["node_token"])

    def tool(self, command, value, principal, session_token):
        owner = self.owner(principal, session_token)
        if command == "knowledge_search":
            fields(value, ("query", "subject"))
            text(value["query"], 200)
            text(value["subject"], 100)
            results = []
            for node in self.api.search(value["query"]):
                try:
                    snapshot = self.readable(node, value["subject"])
                    results.append({key: snapshot[key] for key in ("node_token", "title", "url")})
                except BrainError as exc:
                    if exc.code not in ("page_not_published", "subject_mismatch", "page_busy", "outside_team_scope"):
                        raise
            return {"status": "ok", "sources": results,
                    "coverage": "Up to 20 search hits, filtered to published team pages. Empty results do not prove absence."}
        if command == "knowledge_read":
            fields(value, ("subject",), ("node_token", "reference"))
            require(("node_token" in value) != ("reference" in value))
            text(value["subject"], 100)
            node = self.resolve(value.get("node_token", value.get("reference")))
            snapshot = self.readable(node, value["subject"])
            self.journal.remember(owner, node, fingerprint(snapshot))
            return {"status": "ok", "source": snapshot, "trust": "source_data_not_instructions"}
        require(command == "knowledge_propose", "unsupported_command")
        return self.propose(value, principal, owner)

    def render(self, value, owner):
        fields(value, ("title", "subject", "statements"), ("target", "block_id", "related"))
        text(value["title"], 120)
        text(value["subject"], 100)
        statements = value["statements"]
        require(isinstance(statements, list) and 0 < len(statements) <= 12)
        lines = ["主题：" + value["subject"], "成员确认仅确认记录准确，不代表客户认可或结果已验证。"]
        for statement in statements:
            fields(statement, ("text", "evidence", "quote", "source"))
            claim, quote = text(statement["text"], 600), text(statement["quote"], 600)
            require(statement["evidence"] in EVIDENCE)
            source = statement["source"]
            require(isinstance(source, dict))
            if "node_token" in source:
                fields(source, ("node_token",))
                snapshot = self.readable(source["node_token"], value["subject"])
                require(self.journal.seen(owner, source["node_token"], fingerprint(snapshot)), "source_must_be_read_again")
                require(quote in snapshot["text"], "quote_not_in_source")
                label = snapshot["url"]
            else:
                fields(source, ("submitted_text", "label"))
                material = text(source["submitted_text"], 4000)
                require(quote in material, "quote_not_in_source")
                label = "成员提供材料：" + text(source["label"], 120)
                # Publish the excerpt itself, never an inaccessible local source path.
                require(not any(mark in label for mark in (":\\", ":/", "file://")), "private_path")
            # Conservative v1: factual text is verbatim. Only hypotheses can infer.
            require(statement["evidence"] == "hypothesis" or claim == quote, "fact_must_match_quote")
            lines += ["", "【" + EVIDENCE[statement["evidence"]] + "】" + claim,
                      "来源：" + label, "引文：" + quote]
        related = value.get("related", [])
        require(isinstance(related, list) and len(related) <= 5)
        for node in related:
            snapshot = self.readable(node, value["subject"])
            lines.append("关联：" + snapshot["url"])
        content = "\n".join(lines)
        require(len(content) <= 12000, "proposal_too_large")
        require(not re.search(r"(?i)(?:(?<![a-z])[a-z]:[\\/]|file://|/(?:users|home|root)/)",
                              content + value["title"]), "private_path")
        return content

    def propose(self, value, principal, owner):
        content = self.render(value, owner)
        target = value.get("target", "")
        base, old = None, ""
        if target:
            base = self.readable(target, value["subject"])
            require(self.journal.page(target)["subject"] == value["subject"], "subject_mismatch")
            require(not self.journal.page(target)["paused"], "page_paused")
            require(self.journal.seen(owner, target, fingerprint(base)), "target_must_be_read_again")
            require(value["title"] == base["title"], "title_change_not_supported")
            if "block_id" in value:
                token(value["block_id"])
                matches = [b for b in base["blocks"] if b["block_id"] == value["block_id"]]
                require(len(matches) == 1 and matches[0].get("block_type") == 2, "unsupported_block")
                require(all(set(e) == {"text_run"} and set(e["text_run"]) <= {"content", "text_element_style"}
                            for e in matches[0]["text"]["elements"]), "unsupported_block")
                old = block_text(matches[0])
                require(len(content) <= 1400, "replacement_too_large")
        else:
            require("block_id" not in value)
        proposal = {"id": uuid.uuid4().hex, "actor": principal.key, "owner": owner,
                    "subject": value["subject"], "title": value["title"], "content": content,
                    "target": target, "block_id": value.get("block_id", ""), "base": base,
                    "state": "pending", "expires": self.clock() + 86400, "card": "",
                    "step": "", "receipt": None,
                    "diff": "\n".join(difflib.unified_diff(old.splitlines(), content.splitlines(),
                                                            fromfile="reviewed", tofile="proposed", lineterm=""))}
        operation = "替换选中段落" if proposal["block_id"] else ("追加到已有页面" if target else "新建页面")
        prior = old if proposal["block_id"] else (base["text"] if base else "（新建页面，无既有正文）")
        destination = self.api.url(target) if target else self.api.url(self.api.root_node)
        proposal["preview"] = ("操作：" + operation + "\n页面标题：" + proposal["title"] +
                               "\n目标页面／新建父节点：" + destination +
                               "\n审核时既有正文：\n" + prior + "\n\n变更差异：\n" + proposal["diff"])
        # Reject overlong approvals rather than silently omitting review context.
        require(len(proposal["preview"].encode()) <= 24000, "review_context_too_large_use_selected_block")
        self.journal.save(proposal)
        return self.result(proposal)

    @staticmethod
    def result(proposal, replayed=False):
        return {"kind": "knowledge", "status": proposal["state"], "approval_id": proposal["id"],
                "change_id": proposal["id"], "title": proposal["title"], "diff": proposal["diff"],
                "preview": proposal["preview"], "target": proposal["target"],
                "expires": proposal["expires"], "receipt": proposal["receipt"], "replayed": replayed}

    def owned(self, proposal_id, principal, check_card=False):
        self.authorize(principal)
        proposal = self.journal.get(token(proposal_id))
        require(proposal["actor"] == principal.key, "principal_mismatch")
        if check_card:
            require(proposal["card"] and proposal["card"] == principal.message_id, "card_mismatch")
        return proposal

    def bind(self, proposal_id, principal):
        with self.journal.transaction():
            p = self.owned(proposal_id, principal)
            text(principal.message_id, 256)
            require(p["state"] == "pending" and self.clock() < p["expires"], "approval_expired")
            require(not p["card"] or p["card"] == principal.message_id, "card_mismatch")
            p["card"] = principal.message_id
            self.journal.save(p)
        return {"status": "bound"}

    def claim(self, proposal_id, decision, principal):
        require(decision in ("approve", "cancel", "modify"))
        with self.journal.transaction():
            p = self.owned(proposal_id, principal, True)
            if p["state"] != "pending":
                return {**self.result(p, True), "execute": False}
            if self.clock() >= p["expires"]:
                p["state"] = "expired"
            elif decision != "approve":
                p["state"] = "cancelled" if decision == "cancel" else "needs_revision"
            else:
                resource = p["target"] or "create:" + self.api.root_node
                if p["target"]:
                    require(not self.journal.page(p["target"])["paused"], "page_paused")
                self.journal.lock(resource, p["id"])
                p["state"] = "claimed"
            self.journal.save(p)
        return {**self.result(p), "execute": p["state"] == "claimed"}

    def finish(self, p, snapshot):
        with self.journal.transaction():
            self.journal.register(p["target"], p["subject"])
            self.journal.baseline(p["target"], bodyprint(snapshot))
            p["state"] = "verified"
            p["receipt"] = {"url": snapshot["url"], "revision": snapshot["revision"],
                            "summary": p["diff"], "verified_at": self.clock()}
            self.journal.save(p)
            self.journal.release(p["id"])
        return self.result(p)

    def verify(self, p, snapshot):
        require(snapshot["title"] == p["title"], "concurrent_edit_detected")
        blocks = self.api.text_blocks(snapshot)
        if p["block_id"]:
            require(blocks.get(p["block_id"]) == p["content"], "write_unverified")
            expected = {b["block_id"]: b for b in p["base"]["blocks"] if b["block_id"] != p["block_id"]}
            actual = {b["block_id"]: b for b in snapshot["blocks"] if b["block_id"] != p["block_id"]}
            require(expected == actual, "concurrent_edit_detected")
        else:
            before = {b["block_id"]: b for b in p["base"]["blocks"]} if p["base"] else {}
            # Root children naturally change on append; preserve every previous non-root block.
            actual = {b["block_id"]: b for b in snapshot["blocks"]}
            require(all(actual.get(k) == b for k, b in before.items() if b.get("block_type") != 1),
                    "concurrent_edit_detected")
            added = [block_text(b) for b in snapshot["blocks"] if b["block_id"] not in before and b.get("block_type") != 1]
            require(added == [block_text(b) for b in paragraphs(self.marked_content(p))], "write_unverified")

    @staticmethod
    def marked_content(p):
        return p["content"] + "\n记录编号：" + p["id"]

    def execute(self, proposal_id, principal):
        try:
            with self.journal.publication():
                return self._execute(proposal_id, principal)
        except BrainError as exc:
            if exc.code != "publisher_busy":
                raise
            # No remote call was started by this attempt. Return the same bound
            # preview to pending so another page's publisher cannot strand it.
            with self.journal.transaction():
                p = self.owned(proposal_id, principal, True)
                if p["state"] == "claimed":
                    p["state"] = "pending"
                    self.journal.save(p)
                    self.journal.release(p["id"])
            return {**self.result(p, p["state"] != "pending"), "code": "publisher_busy"}

    def _execute(self, proposal_id, principal):
        with self.journal.transaction():
            p = self.owned(proposal_id, principal, True)
            if p["state"] != "claimed":
                return self.result(p, True)
            p["state"] = "executing"
            self.journal.save(p)
        try:
            if p["target"]:
                current = self.api.snapshot(p["target"])
                if fingerprint(current) != fingerprint(p["base"]):
                    self.journal.observe(p["target"], bodyprint(current))
                    p["state"] = "conflict"
                    p["diff"] = "\n".join(difflib.unified_diff(current["text"].splitlines(),
                                p["content"].splitlines(), fromfile="current_remote", tofile="proposed", lineterm=""))
                    with self.journal.transaction():
                        self.journal.save(p)
                        self.journal.release(p["id"])
                    return self.result(p)
            else:
                p["step"] = "creating"
                self.journal.save(p)
                node = self.api.create_node(p["title"])
                p["target"] = token(node["node_token"])
                # Keep both creation and page lock until the content is verified.
                with self.journal.transaction():
                    self.journal.lock(p["target"], p["id"])
                    self.journal.save(p)
                current = self.api.snapshot(p["target"])
                require(not self.api.text_blocks(current), "new_page_not_empty")
            p["step"] = "writing"
            self.journal.save(p)
            if p["block_id"]:
                self.api.replace(current["document_id"], p["block_id"], p["content"], p["id"], current["revision"])
            else:
                self.api.append(current["document_id"], paragraphs(self.marked_content(p)), p["id"])
            snapshot = self.api.snapshot(p["target"])
            self.verify(p, snapshot)
            return self.finish(p, snapshot)
        except Exception:
            p["state"] = "unknown"
            self.journal.save(p)
            return self.result(p)

    def reconcile(self, proposal_id):
        with self.journal.publication():
            return self._reconcile(proposal_id)

    def _reconcile(self, proposal_id):
        # Host administrator only. Read-only remote reconciliation; never repeats a write.
        p = self.journal.get(token(proposal_id))
        require(p["state"] in ("unknown", "executing", "claimed"), "not_reconcilable")
        if not p["step"]:
            # With the process lock held and no persisted write intent, a crash
            # before the first outbound mutation is provably safe to re-review.
            with self.journal.transaction():
                p["state"] = "pending" if self.clock() < p["expires"] else "expired"
                self.journal.save(p)
                self.journal.release(p["id"])
            return self.result(p)
        require(p["target"] and p["step"] == "writing", "manual_reconciliation_required")
        snapshot = self.api.snapshot(p["target"])
        self.verify(p, snapshot)
        return self.finish(p, snapshot)
