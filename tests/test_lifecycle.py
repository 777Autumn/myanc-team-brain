import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from team_brain.contracts import BrainError, paragraph, strict_json
from team_brain.host import dispatch
from team_brain.journal import Journal
from team_brain.service import Brain, bodyprint
from tests.fakes import FakeFeishu, PRINCIPALS


def material(quote="试用系统账号约 20 个，现阶段只能导出 Excel。", subject="synthetic-customer-a"):
    return {"title": "虚构客户访谈", "subject": subject, "statements": [{"text": quote,
            "evidence": "partner_report", "quote": quote,
            "source": {"submitted_text": quote, "label": "虚构逐字稿"}}]}


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.journal = Journal(self.path)
        self.api = FakeFeishu()
        self.now = 1_000_000
        self.brain = Brain(self.journal, self.api, PRINCIPALS, lambda: self.now)
        self.actor = PRINCIPALS[0]
        self.callback = replace(self.actor, message_id="exact-card")

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def proposal(self, value=None):
        return self.brain.tool("knowledge_propose", value or material(), self.actor, "session-a")["approval_id"]

    def approve(self, proposal_id):
        self.brain.bind(proposal_id, self.callback)
        return self.brain.claim(proposal_id, "approve", self.callback)

    def publish(self, value=None):
        identifier = self.proposal(value)
        self.approve(identifier)
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "verified")
        return self.journal.get(identifier)["target"]

    def read(self, node, subject="synthetic-customer-a", actor=None, session="session-a"):
        return self.brain.tool("knowledge_read", {"node_token": node, "subject": subject}, actor or self.actor, session)

    def test_cancelled_status_is_current_even_when_page_is_unchanged(self):
        node = self.publish()
        before = self.read(node)["source"]["text"]
        value = material("还需核对虚构导出字段。")
        value["target"] = node
        identifier = self.proposal(value)
        self.brain.bind(identifier, self.callback)
        self.brain.claim(identifier, "cancel", self.callback)
        frozen = self.journal.get(identifier)
        writes = self.api.writes
        result = self.brain.tool("knowledge_status", {"approval_id": identifier}, self.actor, "next-turn-token")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["proposal"]["state"], "cancelled")
        self.assertEqual(result["proposal"]["next_action"], "none")
        self.assertEqual(result["proposal"]["summary"], "已取消，不会发布。")
        self.assertEqual(self.journal.get(identifier), frozen)
        self.assertEqual(self.api.writes, writes)
        self.assertEqual(self.read(node)["source"]["text"], before)

    def test_status_rejects_other_principals_and_extra_fields(self):
        identifier = self.proposal()
        for actor in PRINCIPALS[1:]:
            with self.subTest(actor=actor.project), self.assertRaisesRegex(BrainError, "principal_mismatch"):
                self.brain.tool("knowledge_status", {"approval_id": identifier}, actor, "session-a")
        for key in ("session_key", "chat_id", "project"):
            actor = replace(self.actor, **{key: "other-conversation"})
            self.brain.allowed.add(actor.key)
            with self.subTest(key=key), self.assertRaisesRegex(BrainError, "principal_mismatch"):
                self.brain.tool("knowledge_status", {"approval_id": identifier}, actor, "session-a")
        for extra in ("principal", "subject", "decision", "session_token", "target"):
            with self.subTest(extra=extra), self.assertRaisesRegex(BrainError, "invalid_input"):
                self.brain.tool("knowledge_status", {"approval_id": identifier, extra: "fake"}, self.actor, "s")

    def test_status_expiry_and_uncertainty_do_not_mutate_or_suggest_approval(self):
        identifier = self.proposal()
        for state, expected in (("pending", "expired"), ("claimed", "claimed"), ("executing", "executing"),
                                ("unknown", "unknown"), ("conflict", "conflict"), ("needs_revision", "needs_revision")):
            with self.subTest(state=state):
                p = self.journal.get(identifier)
                p["state"] = state
                self.journal.save(p)
                self.now = p["expires"]
                result = self.brain.tool("knowledge_status", {"approval_id": identifier}, self.actor, "s")["proposal"]
                self.assertEqual(result["state"], expected)
                self.assertNotEqual(result["next_action"], "review_card")
                self.assertEqual(self.journal.get(identifier), p)
        self.assertEqual(self.api.writes, 0)

    def test_status_verified_returns_receipt_without_frozen_draft_or_card_binding(self):
        identifier = self.proposal()
        self.approve(identifier)
        self.brain.execute(identifier, self.callback)
        result = self.brain.tool("knowledge_status", {"approval_id": identifier}, self.actor, "s")
        self.assertEqual(result["proposal"]["state"], "verified")
        self.assertEqual(result["proposal"]["next_action"], "none")
        self.assertEqual(result["proposal"]["receipt"]["url"], self.journal.get(identifier)["receipt"]["url"])
        for private in ("content", "diff", "preview", "owner", "actor", "card"):
            self.assertNotIn(private, result["proposal"])

    def test_status_reads_legacy_proposal_without_migration_or_card_effect(self):
        identifier = self.proposal()
        unbound = self.brain.tool("knowledge_status", {"approval_id": identifier}, self.actor, "s")
        self.assertEqual(unbound["proposal"]["next_action"], "reconcile")
        self.brain.bind(identifier, self.callback)
        p = self.journal.get(identifier)
        del p["review"]
        self.journal.save(p)
        result = self.brain.tool("knowledge_status", {"approval_id": identifier}, self.actor, "s")
        self.assertEqual(result["proposal"]["state"], "pending")
        self.assertEqual(result["proposal"]["next_action"], "review_card")
        self.assertEqual(self.journal.get(identifier), p)
        self.assertEqual(self.api.writes, 0)

    def test_meeting_capture_approval_and_reuse_across_six_environments(self):
        self.assertEqual(self.brain.tool("knowledge_search", {"query": "会前准备", "subject": "synthetic-customer-a"},
                                        self.actor, "session-a")["sources"], [])
        proposal_id = self.proposal()
        self.assertEqual(self.api.writes, 0)
        self.assertTrue(self.approve(proposal_id)["execute"])
        self.assertEqual(self.brain.execute(proposal_id, self.callback)["status"], "verified")
        node = self.journal.get(proposal_id)["target"]
        for actor in PRINCIPALS:
            source = self.read(node, actor=actor)["source"]
            self.assertIn("伙伴转述", source["text"])
            self.assertIn("约 20 个", source["text"])
        self.read(node)
        update = material("客户目前未提供接口文档。")
        update["target"] = node
        update_id = self.proposal(update)
        self.approve(update_id)
        self.assertEqual(self.brain.execute(update_id, self.callback)["status"], "verified")
        reused = self.read(node)["source"]["text"]
        self.assertIn("未提供接口", reused)
        self.assertIn("导出 Excel", reused)

    def test_notes_only_publish_and_reuse_without_repeating_facts(self):
        node = self.publish()
        source = self.read(node)["source"]
        quote = material()["statements"][0]["quote"]
        value = {"title": source["title"], "subject": "synthetic-customer-a", "target": node,
                 "statements": [], "notes": [
                     {"kind": kind, "text": wording, "quote": quote, "source": {"node_token": node}}
                     for kind, wording in (("suggestion", "建议单独核对接口文档。"), ("question", "公司员工总数是多少？"))]}
        identifier = self.proposal(value)
        proposal = self.journal.get(identifier)
        self.assertEqual(proposal["review"]["operation"], "append")
        self.assertEqual(proposal["review"]["before"], source["text"])
        self.assertEqual(proposal["review"]["after"], proposal["content"])
        self.assertNotIn("【伙伴转述", proposal["content"])
        self.assertIn("【整理建议】", proposal["content"])
        self.assertIn("【待核实问题】", proposal["content"])
        self.assertIn("依据引文：" + quote, proposal["content"])
        self.approve(identifier)
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "verified")
        self.assertIn("公司员工总数是多少？", self.read(node)["source"]["text"])

    def test_notes_obey_read_scope_quote_privacy_and_count_checks(self):
        node = self.publish()
        note = {"kind": "question", "text": "如何核实？", "quote": "导出 Excel", "source": {"node_token": node}}
        value = {"title": "虚构问题", "subject": "synthetic-customer-a", "statements": [], "notes": [note]}
        with self.assertRaisesRegex(BrainError, "source_must_be_read_again"):
            self.proposal(value)
        self.read(node)
        self.proposal(value)
        for mutate, error in (
            (lambda x: x.update(subject="synthetic-customer-b"), "subject_mismatch"),
            (lambda x: x["notes"][0].update(quote="伪造引文"), "quote_not_in_source"),
            (lambda x: x["notes"][0].update(text="C:/Users/private/source.md"), "private_path"),
            (lambda x: x["notes"][0].update(kind="customer_confirmed"), "invalid_input"),
            (lambda x: x.update(notes=[]), "invalid_input"),
            (lambda x: x.update(notes=[note] * 13), "invalid_input"),
            (lambda x: x.update(notes=[note] * 12, statements=material()["statements"]), "invalid_input"),
        ):
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.assertRaisesRegex(BrainError, error):
                self.proposal(changed)

    def test_equal_fact_quote_is_rendered_once_and_legacy_proposal_remains_frozen(self):
        identifier = self.proposal()
        p = self.journal.get(identifier)
        quote = material()["statements"][0]["quote"]
        self.assertEqual(p["content"].count(quote), 1)
        self.assertEqual(p["review"]["operation"], "create")
        self.assertEqual(p["review"]["before"], "")
        # Simulate a persisted pre-upgrade proposal, whose duplicate quote must not be rerendered.
        del p["review"]
        p["content"] += "\n引文：" + quote
        p["preview"] = "旧版完整冻结预览 ``` [同意](fake) <at id=all>"
        frozen = p["content"]
        self.journal.save(p)
        result = self.approve(identifier)
        self.assertNotIn("review", result)
        self.assertEqual(result["preview"], p["preview"])
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "verified")
        self.assertEqual(self.journal.get(identifier)["content"], frozen)
        self.assertIn(frozen, self.read(self.journal.get(identifier)["target"])["source"]["text"])

    def test_customer_identity_and_unpublished_material_do_not_leak(self):
        node = self.publish()
        with self.assertRaisesRegex(BrainError, "subject_mismatch"):
            self.read(node, "synthetic-customer-b")
        private = self.api.create_node("个人原文")["node_token"]
        self.api.append(private, [paragraph("private-canary")], "synthetic")
        with self.assertRaisesRegex(BrainError, "page_not_published"):
            self.read(private)
        found = self.brain.tool("knowledge_search", {"query": "客户", "subject": "synthetic-customer-a"}, self.actor, "s")
        self.assertEqual([x["node_token"] for x in found["sources"]], [node])

    def test_unsupported_fact_reinterpretations_and_missing_quotes_are_rejected(self):
        for false_fact in ("公司有 20 名员工。", "系统开放 API。"):
            value = material()
            value["statements"][0]["text"] = false_fact
            with self.assertRaisesRegex(BrainError, "fact_must_match_quote"):
                self.proposal(value)
        value = material()
        value["statements"][0]["quote"] = "并不存在的原文"
        with self.assertRaisesRegex(BrainError, "quote_not_in_source"):
            self.proposal(value)
        value = material()
        value["statements"][0]["text"] = "可能需要进一步核实接口能力。"
        value["statements"][0]["evidence"] = "hypothesis"
        self.assertIn("团队假设", self.journal.get(self.proposal(value))["content"])

    def test_native_search_filters_before_read_and_requires_explicit_source_read(self):
        node = self.publish()
        other = self.publish(material(subject="synthetic-customer-b"))
        private = self.api.create_node("private-canary")["node_token"]
        outside = self.publish()
        self.api.outside.add(outside)
        checked, calls = [], []
        scope = self.api.scoped_node
        def scoped(key):
            checked.append(key)
            return scope(key)
        def search(query, cursor):
            calls.append((query, cursor))
            return {"nodes": [private, other, outside] if not cursor else [node],
                    "next_page_token": "next+/=" if not cursor else ""}
        self.api.scoped_node = scoped
        self.api.search = search
        original_snapshot = self.api.snapshot
        self.api.snapshot = lambda _: self.fail("Search must not download document bodies")
        args = {"query": "客户", "subject": "synthetic-customer-a"}
        first = self.brain.tool("knowledge_search", args, self.actor, "search-session")
        self.assertEqual(first["sources"], [])
        self.assertEqual(checked, [outside])
        second = self.brain.tool("knowledge_search", {**args, "page_token": first["next_page_token"]},
                                 self.actor, "search-session")
        self.assertEqual([s["node_token"] for s in second["sources"]], [node])
        self.assertEqual(second["next_page_token"], "")
        self.assertEqual(calls, [("客户", ""), ("客户", "next+/=")])
        self.api.snapshot = original_snapshot
        value = material()
        value["statements"][0]["source"] = {"node_token": node}
        with self.assertRaisesRegex(BrainError, "source_must_be_read_again"):
            self.brain.tool("knowledge_propose", value, self.actor, "search-session")
        self.read(node, session="search-session")
        self.brain.tool("knowledge_propose", value, self.actor, "search-session")

    def test_native_search_rejects_model_scope_and_busy_page(self):
        node = self.publish()
        args = {"query": "客户", "subject": "synthetic-customer-a"}
        for extra in ({"space_id": "personal"}, {"wiki_filter": {}}, {"query": "甲" * 31}):
            with self.assertRaises(BrainError):
                self.brain.tool("knowledge_search", {**args, **extra}, self.actor, "s")
        scope = self.api.scoped_node
        def lock_during_lookup(key):
            result = scope(key)
            self.journal.lock(key, "synthetic-publication")
            return result
        self.api.scoped_node = lock_during_lookup
        self.assertEqual(self.brain.tool("knowledge_search", args, self.actor, "s")["sources"], [])

    def test_catalog_finds_published_methods_when_native_search_misses(self):
        value = material(subject="team")
        value["title"] = "客户初访提纲"
        node = self.publish(value)
        self.publish(material(subject="unrelated-customer"))
        self.api.create_node("未发布原文")
        self.api.search = lambda *_: {"nodes": [], "next_page_token": ""}
        self.assertEqual(self.brain.tool("knowledge_search", {"query": "宴会中心", "subject": "team"},
                                        self.actor, "catalog-session")["sources"], [])
        snapshot = self.api.snapshot
        self.api.snapshot = lambda _: self.fail("Catalog must not read document bodies")
        result = self.brain.tool("knowledge_catalog", {"subject": "team"}, self.actor, "catalog-session")
        self.assertEqual(result["sources"], [{"node_token": node, "title": "客户初访提纲", "url": self.api.url(node)}])
        self.assertEqual(result["next_page_token"], "")
        for actor in PRINCIPALS:
            self.assertEqual(self.brain.tool("knowledge_catalog", {"subject": "team"}, actor, "s")["sources"], result["sources"])
        self.api.snapshot = snapshot
        value["statements"][0]["source"] = {"node_token": node}
        with self.assertRaisesRegex(BrainError, "source_must_be_read_again"):
            self.brain.tool("knowledge_propose", value, self.actor, "catalog-session")
        self.read(node, subject="team", session="catalog-session")
        self.brain.tool("knowledge_propose", value, self.actor, "catalog-session")

    def test_catalog_empty_filtered_page_continues_with_at_most_five_lookups(self):
        nodes = [self.publish(material(subject="team")) for _ in range(7)]
        self.api.outside.update(nodes[:5])
        checks = []
        scoped = self.api.scoped_node
        def observe(node):
            checks.append(node)
            return scoped(node)
        self.api.scoped_node = observe
        self.api.search = lambda *_: self.fail("Catalog must use the published registry, not search")
        first = self.brain.tool("knowledge_catalog", {"subject": "team"}, self.actor, "s")
        self.assertEqual(first["sources"], [])
        self.assertEqual(checks, nodes[:5])
        self.assertTrue(first["next_page_token"])
        self.journal.pause(nodes[5], True)  # Pause blocks publishing, not reading.
        self.api.pages[nodes[5]]["title"] = "当前标题"
        second = self.brain.tool("knowledge_catalog", {"subject": "team", "page_token": first["next_page_token"]},
                                 self.actor, "s")
        self.assertEqual([s["node_token"] for s in second["sources"]], nodes[5:])
        self.assertEqual(second["sources"][0]["title"], "当前标题")
        self.assertEqual(second["next_page_token"], "")
        self.assertEqual(checks, nodes)

    def test_catalog_cursor_is_scoped_and_reauthorizes_every_page(self):
        for _ in range(6):
            self.publish(material(subject="team"))
        cursor = self.brain.tool("knowledge_catalog", {"subject": "team"}, self.actor, "s")["next_page_token"]
        args = {"subject": "team", "page_token": cursor}
        for actor, session, value in ((PRINCIPALS[1], "s", args), (self.actor, "other", args),
                                      (self.actor, "s", {**args, "subject": "customer-a"}),
                                      (self.actor, "s", {**args, "page_token": "node99." + cursor.split(".")[1]})):
            with self.subTest(actor=actor, session=session, value=value):
                with self.assertRaisesRegex(BrainError, "invalid_page_token"):
                    self.brain.tool("knowledge_catalog", value, actor, session)
        for extra in ({"space_id": "other"}, {"principal": {}}, {"query": "title"},
                      {"page_token": "bad"}, {"page_token": "node0.中文"}):
            with self.assertRaises(BrainError):
                self.brain.tool("knowledge_catalog", {"subject": "team", **extra}, self.actor, "s")
        self.brain.allowed.remove(self.actor.key)
        with self.assertRaisesRegex(BrainError, "principal_mismatch"):
            self.brain.tool("knowledge_catalog", args, self.actor, "s")

    def test_catalog_skips_publication_locks_but_propagates_failures(self):
        node = self.publish(material(subject="team"))
        scoped = self.api.scoped_node
        self.journal.lock(node, "publishing")
        self.api.scoped_node = lambda _: self.fail("Locked page must not reach API")
        args = {"subject": "team"}
        self.assertEqual(self.brain.tool("knowledge_catalog", args, self.actor, "s")["sources"], [])
        self.journal.release("publishing")
        def locked_during_lookup(key):
            result = scoped(key)
            self.journal.lock(key, "publishing")
            return result
        self.api.scoped_node = locked_during_lookup
        self.assertEqual(self.brain.tool("knowledge_catalog", args, self.actor, "s")["sources"], [])
        self.journal.release("publishing")
        for code in ("api_rejected", "api_transport_error", "invalid_response"):
            def failed(_):
                raise BrainError(code)
            self.api.scoped_node = failed
            with self.assertRaisesRegex(BrainError, code):
                self.brain.tool("knowledge_catalog", args, self.actor, "s")
        self.journal.close()
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            self.brain.tool("knowledge_catalog", args, self.actor, "s")

    def test_catalog_keyset_survives_restart_and_earlier_registry_changes(self):
        nodes = [self.publish(material(subject="team")) for _ in range(6)]
        cursor = self.brain.tool("knowledge_catalog", {"subject": "team"}, self.actor, "s")["next_page_token"]
        self.journal.db.execute("DELETE FROM pages WHERE node=?", (nodes[0],))
        self.journal.close()
        self.journal = Journal(self.path)
        self.brain = Brain(self.journal, self.api, PRINCIPALS)
        last = self.brain.tool("knowledge_catalog", {"subject": "team", "page_token": cursor}, self.actor, "s")
        self.assertEqual([source["node_token"] for source in last["sources"]], nodes[5:])
        self.assertEqual(last["next_page_token"], "")

    def test_source_receipts_are_bound_to_actor_and_live_session(self):
        node = self.publish()
        self.read(node)
        value = material()
        value["statements"][0]["source"] = {"node_token": node}
        for actor, session in ((PRINCIPALS[1], "session-a"), (self.actor, "session-b")):
            with self.assertRaisesRegex(BrainError, "source_must_be_read_again"):
                self.brain.tool("knowledge_propose", value, actor, session)
        self.proposal(value)

    def test_unapproved_copied_card_wrong_member_and_duplicate_confirmation(self):
        identifier = self.proposal()
        self.assertEqual(self.api.writes, 0)
        with self.assertRaisesRegex(BrainError, "card_mismatch"):
            self.brain.execute(identifier, self.actor)
        self.brain.bind(identifier, self.callback)
        for callback in (replace(self.callback, message_id="copied-card"), replace(PRINCIPALS[1], message_id="exact-card")):
            with self.assertRaises(BrainError):
                self.brain.claim(identifier, "approve", callback)
        self.assertTrue(self.brain.claim(identifier, "approve", self.callback)["execute"])
        self.assertFalse(self.brain.claim(identifier, "approve", self.callback)["execute"])
        self.brain.execute(identifier, self.callback)
        writes = self.api.writes
        self.assertFalse(self.brain.claim(identifier, "approve", self.callback)["execute"])
        self.brain.execute(identifier, self.callback)
        self.assertEqual(writes, self.api.writes)

    def test_expired_and_modified_proposals_cannot_write(self):
        identifier = self.proposal()
        self.brain.bind(identifier, self.callback)
        self.now += 86401
        self.assertEqual(self.brain.claim(identifier, "approve", self.callback)["status"], "expired")
        self.brain.execute(identifier, self.callback)
        identifier = self.proposal()
        self.brain.bind(identifier, self.callback)
        self.assertEqual(self.brain.claim(identifier, "modify", self.callback)["status"], "needs_revision")
        self.brain.execute(identifier, self.callback)
        self.assertEqual(self.api.writes, 0)

    def test_stale_review_generates_new_diff_and_requires_new_proposal(self):
        node = self.publish()
        self.read(node)
        value = material("新增访谈事实。")
        value["target"] = node
        identifier = self.proposal(value)
        self.api.append(node, [paragraph("管理员修改")], "external")
        self.approve(identifier)
        writes = self.api.writes
        result = self.brain.execute(identifier, self.callback)
        self.assertEqual(result["status"], "conflict")
        self.assertIn("current_remote", result["diff"])
        self.assertEqual(writes, self.api.writes)
        self.assertFalse(self.brain.claim(identifier, "approve", self.callback)["execute"])

    def test_page_serialization_and_pause(self):
        node = self.publish()
        self.read(node)
        value = material()
        value["target"] = node
        first, second = self.proposal(value), self.proposal(value)
        self.approve(first)
        with self.assertRaisesRegex(BrainError, "page_busy"):
            self.approve(second)
        with self.assertRaisesRegex(BrainError, "page_busy"):
            self.journal.pause(node, True)
        self.brain.execute(first, self.callback)
        self.journal.pause(node, True)
        with self.assertRaisesRegex(BrainError, "page_paused"):
            self.brain.claim(second, "approve", self.callback)

    def test_append_timeout_reconciles_after_restart_without_repeating_write(self):
        identifier = self.proposal()
        self.approve(identifier)
        self.api.failure = "append"
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "unknown")
        node = self.journal.get(identifier)["target"]
        with self.assertRaises(BrainError):
            self.read(node)
        writes = self.api.writes
        self.journal.close()
        self.journal = Journal(self.path)
        self.brain = Brain(self.journal, self.api, PRINCIPALS)
        self.assertEqual(self.brain.reconcile(identifier)["status"], "verified")
        self.assertEqual(writes, self.api.writes)
        self.read(node)

    def test_unknown_creation_never_recreates_and_holds_scope_lock(self):
        identifier = self.proposal()
        self.approve(identifier)
        self.api.failure = "create"
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "unknown")
        with self.assertRaisesRegex(BrainError, "manual_reconciliation_required"):
            self.brain.reconcile(identifier)
        with self.assertRaisesRegex(BrainError, "page_busy"):
            self.approve(self.proposal())
        self.assertEqual(self.api.writes, 1)

    def test_crash_after_claim_before_write_can_be_reviewed_again(self):
        identifier = self.proposal()
        self.approve(identifier)
        self.assertEqual(self.brain.reconcile(identifier)["status"], "pending")
        self.assertEqual(self.api.writes, 0)
        self.assertTrue(self.brain.claim(identifier, "approve", self.callback)["execute"])
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "verified")

    def test_partial_append_cannot_be_verified_or_retried(self):
        value = material("甲" * 500)
        value["statements"] *= 3
        identifier = self.proposal(value)
        self.approve(identifier)
        self.api.failure = "partial"
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "unknown")
        with self.assertRaisesRegex(BrainError, "write_unverified"):
            self.brain.reconcile(identifier)
        writes = self.api.writes
        self.brain.execute(identifier, self.callback)
        self.assertEqual(writes, self.api.writes)

    def test_local_block_replacement_preserves_other_blocks(self):
        node = self.publish()
        self.api.append(node, [paragraph("不要修改的既有段落")], "external")
        self.journal.baseline(node, bodyprint(self.api.snapshot(node)))
        self.read(node)
        value = material("已核对记录内容。")
        value.update(target=node, block_id="block0")
        identifier = self.proposal(value)
        self.approve(identifier)
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "verified")
        self.assertIn("不要修改的既有段落", self.read(node)["source"]["text"])

    def test_direct_admin_edit_pauses_agent_writes(self):
        node = self.publish()
        self.api.append(node, [paragraph("管理员直接修改")], "external")
        self.read(node)
        value = material()
        value["target"] = node
        with self.assertRaisesRegex(BrainError, "page_paused"):
            self.proposal(value)

    def test_title_change_during_write_cannot_be_marked_verified(self):
        node = self.publish()
        self.read(node)
        value = material("新的原话。")
        value["target"] = node
        identifier = self.proposal(value)
        self.approve(identifier)
        append = self.api.append
        def concurrent_rename(*args):
            append(*args)
            self.api.pages[node]["title"] = "管理员同时修改标题"
        self.api.append = concurrent_rename
        self.assertEqual(self.brain.execute(identifier, self.callback)["status"], "unknown")
        with self.assertRaisesRegex(BrainError, "concurrent_edit_detected"):
            self.brain.reconcile(identifier)

    def test_document_urls_stay_within_registered_wiki_scope(self):
        node = self.publish()
        for path in ("wiki/", "docx/"):
            result = self.brain.tool("knowledge_read", {"reference": self.api.wiki_origin + "/" + path + node,
                                     "subject": "synthetic-customer-a"}, self.actor, "s")
            self.assertEqual(result["source"]["node_token"], node)
        with self.assertRaisesRegex(BrainError, "outside_team_scope"):
            self.brain.tool("knowledge_read", {"reference": "https://elsewhere.feishu.cn/wiki/" + node,
                            "subject": "synthetic-customer-a"}, self.actor, "s")

    def test_private_paths_and_unselected_personal_material_are_rejected(self):
        for path in ("D:/private/client.md", "C:\\Users\\Someone\\memo.md", "file:///private/doc", "/home/private/doc"):
            with self.assertRaisesRegex(BrainError, "private_path"):
                self.proposal(material(path))

    def test_reconcile_cannot_race_an_active_publisher(self):
        other = Journal(self.path)
        try:
            with self.journal.publication():
                with self.assertRaisesRegex(BrainError, "publisher_busy"):
                    with other.publication():
                        self.fail("second publisher entered")
        finally:
            other.close()

    def test_two_different_pages_busy_publisher_returns_retryable_bound_preview(self):
        first, second = self.publish(), self.publish()
        ids = []
        for node in (first, second):
            self.read(node)
            value = material()
            value["target"] = node
            identifier = self.proposal(value)
            self.approve(identifier)
            ids.append(identifier)
        with self.journal.publication():
            result = self.brain.execute(ids[1], self.callback)
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(self.journal.locked(second))
        self.assertTrue(self.journal.locked(first))
        self.assertEqual(self.brain.execute(ids[0], self.callback)["status"], "verified")
        self.assertTrue(self.brain.claim(ids[1], "approve", self.callback)["execute"])
        self.assertEqual(self.brain.execute(ids[1], self.callback)["status"], "verified")

    def test_same_customer_different_targets_have_distinct_complete_previews(self):
        nodes = [self.publish(), self.publish()]
        previews = []
        for node in nodes:
            self.read(node)
            value = material("追加的原话。")
            value["target"] = node
            p = self.journal.get(self.proposal(value))
            previews.append(p["preview"])
            self.assertIn(self.api.url(node), p["preview"])
            self.assertIn("追加到已有页面", p["preview"])
            self.assertIn(self.api.snapshot(node)["text"], p["preview"])
            self.assertIn(value["title"], p["preview"])
        self.assertNotEqual(previews[0], previews[1])

    def test_model_cannot_smuggle_identity_or_publish_operations(self):
        value = material()
        value["principal"] = self.actor.identity()
        with self.assertRaises(BrainError):
            self.proposal(value)
        for command in ("approve", "execute", "delete", "permissions"):
            with self.assertRaisesRegex(BrainError, "unsupported_command"):
                self.brain.tool(command, {}, self.actor, "s")
        for raw in ('{"a":1,"a":2}', '{"x":NaN}'):
            with self.assertRaises(BrainError):
                strict_json(raw)
        with self.assertRaises(BrainError):
            dispatch(self.brain, {"operation": "tool", "principal": self.actor.identity(), "command": "execute",
                                 "input": {}, "session_token": "s", "approval_id": "smuggled"})


if __name__ == "__main__":
    unittest.main()
