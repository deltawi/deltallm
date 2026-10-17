"""Lexical search over the MCP tools that one request may use.

With `tool_search: true`, the model first gets only the search tool. Each
search adds the best matching tools to the next model call. This keeps large
tool catalogs out of the prompt. The index is built per request from tools the
caller can already see, so a search never shows a tool that the caller cannot
call.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

SEARCH_TOOL_NAME = "mcp_tool_search"
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 10
MAX_QUERY_CHARS = 512
# Upper bound for tools that searches can add to one request.
MAX_LOADED_TOOLS = 32

_TOKEN = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_K1 = 1.2
_B = 0.75
# A match in the tool name is a stronger signal than one in the description.
_NAME_WEIGHT = 3


@dataclass(frozen=True, slots=True)
class SearchableTool:
    name: str
    description: str | None
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSearchHit:
    name: str
    description: str | None
    score: float


def search_tool_definition() -> dict[str, Any]:
    return {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Search the available tools. Call this first to find tools for the task. "
            "The tools that match become available in your next turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words that describe the task, for example 'create github issue'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "description": f"Maximum number of tools to return. Default {DEFAULT_SEARCH_LIMIT}.",
                },
            },
            "required": ["query"],
        },
    }


def tokenize(text: str) -> list[str]:
    spaced = _CAMEL_BOUNDARY.sub(" ", text)
    return _TOKEN.findall(spaced.lower())


class ToolSearchIndex:
    """BM25 index. Build cost and memory are linear in the request's tool count."""

    def __init__(self, tools: list[SearchableTool]) -> None:
        self._tools = tools
        self._documents = [Counter(_document_tokens(tool)) for tool in tools]
        self._lengths = [sum(document.values()) for document in self._documents]
        self._average_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        frequencies: Counter[str] = Counter()
        for document in self._documents:
            frequencies.update(document.keys())
        count = len(self._documents)
        self._idf = {
            term: math.log(1 + (count - seen + 0.5) / (seen + 0.5))
            for term, seen in frequencies.items()
        }

    def __len__(self) -> int:
        return len(self._tools)

    def search(self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT) -> list[ToolSearchHit]:
        terms = set(tokenize(query[:MAX_QUERY_CHARS]))
        limit = max(1, min(MAX_SEARCH_LIMIT, limit))
        if not terms or not self._tools:
            return []
        scored: list[ToolSearchHit] = []
        for tool, document, length in zip(self._tools, self._documents, self._lengths):
            score = 0.0
            for term in terms:
                frequency = document.get(term, 0)
                if not frequency:
                    continue
                normalizer = _K1 * (1 - _B + _B * length / (self._average_length or 1))
                score += self._idf[term] * frequency * (_K1 + 1) / (frequency + normalizer)
            if score > 0:
                scored.append(ToolSearchHit(tool.name, tool.description, score))
        scored.sort(key=lambda hit: (-hit.score, hit.name))
        return scored[:limit]


def _document_tokens(tool: SearchableTool) -> list[str]:
    tokens = tokenize(tool.name) * _NAME_WEIGHT
    tokens.extend(tokenize(tool.description or ""))
    properties = tool.input_schema.get("properties")
    if isinstance(properties, dict):
        for name, schema in properties.items():
            tokens.extend(tokenize(str(name)))
            if isinstance(schema, dict) and isinstance(schema.get("description"), str):
                tokens.extend(tokenize(schema["description"]))
    return tokens
