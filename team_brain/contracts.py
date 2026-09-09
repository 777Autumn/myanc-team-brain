"""Small wire contracts shared by the host and its offline tests."""

import hashlib
import json
import re
from dataclasses import dataclass


class BrainError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code="invalid_input"):
    if not condition:
        raise BrainError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def invalid_constant(_):
        raise BrainError("invalid_input")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def fields(value, required, optional=()):
    require(isinstance(value, dict))
    require(set(required) <= value.keys() <= set(required) | set(optional))


def text(value, limit=2000):
    require(isinstance(value, str) and 0 < len(value) <= limit)
    require(not any(ord(c) < 32 and c not in "\n\t\r" for c in value))
    return value


def token(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value))
    return value


@dataclass(frozen=True)
class Principal:
    platform: str
    user_id: str
    chat_id: str
    session_key: str
    project: str
    message_id: str = ""

    @classmethod
    def parse(cls, value):
        fields(value, ("platform", "user_id", "chat_id", "session_key", "project"), ("message_id",))
        for key in ("platform", "user_id", "chat_id", "session_key", "project"):
            text(value[key], 256)
        require(isinstance(value.get("message_id", ""), str) and len(value.get("message_id", "")) <= 256)
        return cls(**value)

    def identity(self):
        return {key: getattr(self, key) for key in
                ("platform", "user_id", "chat_id", "session_key", "project")}

    @property
    def key(self):
        return digest(self.identity())


EVIDENCE = {
    "partner_report": "伙伴转述（待客户核实）",
    "hypothesis": "团队假设（待验证）",
    "customer_confirmed": "客户确认（按所引来源记录）",
    "verified_outcome": "验证结果（按所引证据记录）",
}


def block_text(block):
    for value in block.values():
        if isinstance(value, dict) and isinstance(value.get("elements"), list):
            return "".join(element.get("text_run", {}).get("content", "")
                           for element in value["elements"])
    return ""


def paragraph(content):
    return {"block_type": 2, "text": {"elements": [{"text_run": {"content": content}}]}}


def paragraphs(content):
    # Each text block stays small; the API accepts at most 50 children per call.
    return [paragraph(content[start:start + 1400]) for start in range(0, len(content), 1400)]
