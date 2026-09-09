import unittest

from team_brain.contracts import BrainError
from team_brain.feishu import Feishu, NoRedirect


class FeishuBoundary(unittest.TestCase):
    def client(self):
        return Feishu("synthetic-app", "synthetic-secret", "space", "root", "https://synthetic.feishu.cn")

    def test_scope_walk_rejects_other_space_and_shortcuts(self):
        api = self.client()
        nodes = {"child": {"node_token":"child", "space_id":"space", "obj_type":"docx", "node_type":"origin", "parent_node_token":"root"},
                 "root": {"node_token":"root", "space_id":"space", "obj_type":"docx", "node_type":"origin"}}
        api.node = lambda key: nodes[key]
        self.assertEqual(api.scoped_node("child")["node_token"], "child")
        nodes["root"]["space_id"] = "private-space"
        with self.assertRaisesRegex(BrainError, "outside_team_scope"):
            api.scoped_node("child")
        nodes["child"]["node_type"] = "shortcut"
        with self.assertRaisesRegex(BrainError, "outside_team_scope"):
            api.scoped_node("child")

    def test_api_payloads_keep_fixed_root_and_no_permission_calls(self):
        api = self.client()
        calls = []
        def request(method, path, body=None, params=None):
            calls.append((method, path, body, params))
            return {"node": {"node_token":"created"}}
        api.request = request
        api.create_node("Reviewed title")
        api.append("doc", [{"block_type":2}], "stable-id")
        api.replace("doc", "block", "replacement", "stable-id", 9)
        api.node("doc", "docx")
        self.assertEqual(calls[0][1], "/wiki/v2/spaces/space/nodes")
        self.assertEqual(calls[0][2]["parent_node_token"], "root")
        self.assertIsNone(calls[0][3])  # Wiki creation has no invented idempotency key.
        self.assertEqual(calls[1][3], {"client_token":"stable-id"})
        self.assertEqual(calls[2][3], {"client_token":"stable-id", "document_revision_id":9})
        self.assertEqual(calls[3][3], {"token":"doc", "obj_type":"docx"})

    def test_document_revision_change_is_not_a_stable_read(self):
        api = self.client()
        api.scoped_node = lambda _: {"obj_token":"doc", "title":"Synthetic"}
        revision_reads = []
        def request(method, path, body=None, params=None):
            if path.endswith("/raw_content"):
                return {"content":"Synthetic text"}
            if path.endswith("/blocks"):
                return {"items":[], "has_more":False}
            revision_reads.append(1)
            return {"document":{"revision_id":len(revision_reads)}}
        api.request = request
        with self.assertRaisesRegex(BrainError, "document_changed_during_read"):
            api.snapshot("node")

    def test_fixed_origin_rejects_redirect_and_embedded_credentials(self):
        with self.assertRaisesRegex(BrainError, "unexpected_redirect"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://outside.invalid")
        for origin in ("http://synthetic.feishu.cn", "https://secret@synthetic.feishu.cn", "https://synthetic.feishu.cn:444", "https://synthetic.feishu.cn/path"):
            with self.assertRaises(BrainError):
                Feishu("app", "secret", "space", "root", origin)


if __name__ == "__main__":
    unittest.main()
