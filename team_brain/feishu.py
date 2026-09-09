"""Fixed-origin Feishu OpenAPI client. No permission or deletion endpoints."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .contracts import BrainError, block_text, require, text, token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BrainError("unexpected_redirect")


class Feishu:
    ORIGIN = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id, app_secret, space_id, root_node, wiki_origin):
        self.app_id, self.app_secret = app_id, app_secret
        self.space_id, self.root_node = token(space_id), token(root_node)
        parsed = urllib.parse.urlsplit(wiki_origin)
        require(parsed.scheme == "https" and parsed.hostname and
                parsed.hostname.endswith(".feishu.cn") and not parsed.username and
                not parsed.password and not parsed.port and parsed.path in ("", "/") and
                not parsed.query and not parsed.fragment, "invalid_wiki_origin")
        self.wiki_origin = wiki_origin.rstrip("/")
        self._access_token = ""
        self._expires = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None, params=None, auth=True):
        require(path.startswith("/") and ".." not in path, "invalid_api_path")
        if auth and time.time() >= self._expires:
            data = self.request("POST", "/auth/v3/tenant_access_token/internal",
                                {"app_id": self.app_id, "app_secret": self.app_secret}, auth=False)
            self._access_token = data["tenant_access_token"]
            self._expires = time.time() + max(1, int(data["expire"]) - 60)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = "Bearer " + self._access_token
        url = self.ORIGIN + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method=method, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(request, timeout=12) as response:
                raw = response.read(2_000_001)
                require(len(raw) <= 2_000_000, "response_too_large")
                result = json.loads(raw)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            # Neither remote response bodies nor secrets enter model-visible errors.
            raise BrainError("api_transport_error") from exc
        require(isinstance(result, dict) and result.get("code") == 0, "api_rejected")
        return result.get("data", result)

    def url(self, node_token):
        return self.wiki_origin + "/wiki/" + token(node_token)

    def node(self, node_token, object_type=None):
        params = {"token": token(node_token)}
        if object_type:
            require(object_type == "docx")
            params["obj_type"] = object_type
        return self.request("GET", "/wiki/v2/spaces/get_node", params=params)["node"]

    def scoped_node(self, node_token):
        node = self.node(node_token)
        require(node.get("space_id") == self.space_id and node.get("node_type") == "origin"
                and node.get("obj_type") == "docx", "outside_team_scope")
        current = node
        seen = set()
        for _ in range(32):
            key = current.get("node_token")
            require(current.get("space_id") == self.space_id, "outside_team_scope")
            require(key not in seen, "invalid_wiki_tree")
            seen.add(key)
            if key == self.root_node:
                return node
            parent = current.get("parent_node_token")
            require(parent and current.get("space_id") == self.space_id, "outside_team_scope")
            current = self.node(parent)
            require(current.get("node_type") == "origin", "outside_team_scope")
        raise BrainError("outside_team_scope")

    def children(self):
        items, cursor = [], ""
        for _ in range(100):
            params = {"parent_node_token": self.root_node, "page_size": 50}
            if cursor:
                params["page_token"] = cursor
            data = self.request("GET", f"/wiki/v2/spaces/{self.space_id}/nodes", params=params)
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                return items
            cursor = data.get("page_token", "")
            require(cursor, "incomplete_pagination")
        raise BrainError("incomplete_pagination")

    def search(self, query, page_token=""):
        text(query, 30)
        require(query.strip(), "invalid_input")
        body = {
            "query": query, "wiki_filter": {"space_ids": [self.space_id], "only_title": False},
            "page_size": 5}
        if page_token:
            body["page_token"] = text(page_token, 4096)
        result = self.request("POST", "/search/v2/doc_wiki/search", body)
        found = []
        for item in result.get("res_units", []):
            meta = item.get("result_meta", {})
            # Only Wiki URLs supplied by the API, never arbitrary URLs/redirects.
            parsed = urllib.parse.urlsplit(meta.get("url", ""))
            if (item.get("entity_type") == "WIKI" and parsed.scheme == "https" and
                    parsed.netloc == urllib.parse.urlsplit(self.wiki_origin).netloc and
                    not parsed.query and not parsed.fragment):
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 2 and parts[0] == "wiki":
                    node = token(parts[1])
                    if node not in found:
                        found.append(node)
        cursor = result.get("page_token", "") if result.get("has_more") else ""
        if result.get("has_more"):
            text(cursor, 4096)
            require(cursor != page_token, "incomplete_pagination")
        return {"nodes": found, "next_page_token": cursor}

    def snapshot(self, node_token):
        node = self.scoped_node(node_token)
        document = token(node["obj_token"])
        prefix = f"/docx/v1/documents/{document}"
        before = self.request("GET", prefix)["document"]["revision_id"]
        raw = self.request("GET", prefix + "/raw_content").get("content", "")
        blocks, cursor = [], ""
        for _ in range(100):
            params = {"page_size": 500, "document_revision_id": before}
            if cursor:
                params["page_token"] = cursor
            data = self.request("GET", prefix + "/blocks", params=params)
            blocks.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            cursor = data.get("page_token", "")
            require(cursor, "incomplete_pagination")
        else:
            raise BrainError("incomplete_pagination")
        after = self.request("GET", prefix)["document"]["revision_id"]
        require(before == after, "document_changed_during_read")
        return {"node_token": node_token, "document_id": document,
                "title": node.get("title", ""), "revision": after, "text": raw,
                "blocks": blocks, "url": self.url(node_token),
                "unsupported_blocks": [b["block_id"] for b in blocks
                                       if b.get("block_type") not in set(range(1, 18)) | {22}
                                       or any("elements" in v and any("text_run" not in e for e in v["elements"])
                                              for v in b.values() if isinstance(v, dict))]}

    def create_node(self, title):
        return self.request("POST", f"/wiki/v2/spaces/{self.space_id}/nodes", {
            "obj_type": "docx", "node_type": "origin", "parent_node_token": self.root_node,
            "title": title})["node"]

    def append(self, document_id, blocks, operation_id):
        token(document_id)
        require(0 < len(blocks) <= 50)
        return self.request("POST", f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                            {"children": blocks, "index": -1}, {"client_token": operation_id})

    def replace(self, document_id, block_id, content, operation_id, revision):
        token(document_id), token(block_id)
        return self.request("PATCH", f"/docx/v1/documents/{document_id}/blocks/{block_id}",
                            {"update_text_elements": {"elements": [{"text_run": {"content": content}}]}},
                            {"client_token": operation_id, "document_revision_id": revision})

    @staticmethod
    def text_blocks(snapshot):
        return {b["block_id"]: block_text(b) for b in snapshot["blocks"] if b.get("block_type") != 1}
