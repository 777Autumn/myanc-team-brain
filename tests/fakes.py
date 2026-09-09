import copy

from team_brain.contracts import BrainError, Principal, block_text
from team_brain.feishu import Feishu


PRINCIPALS = [Principal("feishu", "member" + str(n), kind + str(n), kind + ":" + str(n),
                        "project-" + kind + str(n)) for n in range(3) for kind in ("private", "group")]


class FakeFeishu:
    root_node = "teamroot"
    wiki_origin = "https://synthetic.feishu.cn"
    text_blocks = staticmethod(Feishu.text_blocks)

    def __init__(self):
        self.pages = {}
        self.writes = 0
        self.failure = ""
        self.outside = set()

    def url(self, node):
        return "https://synthetic.feishu.cn/wiki/" + node

    def node(self, node, object_type=None):
        return {"node_token": node}

    def snapshot(self, node):
        if node in self.outside:
            raise BrainError("outside_team_scope")
        result = copy.deepcopy(self.pages[node])
        result["text"] = "\n".join(block_text(b) for b in result["blocks"])
        return result

    def search(self, query):
        return list(self.pages)

    def create_node(self, title):
        self.writes += 1
        node = "node" + str(len(self.pages))
        self.pages[node] = {"node_token": node, "document_id": node, "title": title,
                            "revision": 1, "text": "", "blocks": [], "url": self.url(node),
                            "unsupported_blocks": []}
        if self.failure == "create":
            raise TimeoutError()
        return {"node_token": node}

    def append(self, document_id, blocks, operation_id):
        self.writes += 1
        page = self.pages[document_id]
        for b in blocks[:1] if self.failure == "partial" else blocks:
            page["blocks"].append({**copy.deepcopy(b), "block_id": "block" + str(len(page["blocks"]))})
        page["revision"] += 1
        if self.failure in ("append", "partial"):
            raise TimeoutError()

    def replace(self, document_id, block_id, content, operation_id, revision):
        self.writes += 1
        page = self.pages[document_id]
        for b in page["blocks"]:
            if b["block_id"] == block_id:
                b["text"]["elements"] = [{"text_run": {"content": content}}]
        page["revision"] += 1
