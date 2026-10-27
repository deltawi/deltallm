from __future__ import annotations

import base64
from hashlib import sha256

from src.services.audit.payload_compaction import compact_audit_content

_MAX = 1_048_576


def _image_data_url(size: int = 4096) -> str:
    return "data:image/png;base64," + base64.b64encode(b"\x89PNG" * size).decode()


def test_inline_data_url_is_replaced_by_media_type_size_and_hash() -> None:
    url = _image_data_url()
    content = {
        "model": "ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the text"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
    }

    compacted, changed = compact_audit_content(content, max_bytes=_MAX)

    assert changed is True
    parts = compacted["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "Extract the text"}
    placeholder = parts[1]["image_url"]["url"]
    assert placeholder.startswith("[omitted inline data: image/png, ")
    assert f"{len(url)} chars" in placeholder
    assert sha256(url.encode()).hexdigest() in placeholder
    # The caller's request body is never mutated.
    assert content["messages"][0]["content"][1]["image_url"]["url"] == url


def test_raw_base64_fields_are_replaced() -> None:
    data = base64.b64encode(b"\x00\x01" * 4096).decode()
    content = {
        "messages": [
            {
                "content": [
                    {"type": "image", "source": {"type": "base64", "data": data}},
                    {"type": "input_audio", "input_audio": {"data": data, "format": "wav"}},
                ]
            }
        ],
        "data": [{"b64_json": data}],
    }

    compacted, changed = compact_audit_content(content, max_bytes=_MAX)

    assert changed is True
    blocks = compacted["messages"][0]["content"]
    assert blocks[0]["source"]["data"].startswith("[omitted inline data: base64, ")
    assert blocks[0]["source"]["type"] == "base64"
    assert blocks[1]["input_audio"]["format"] == "wav"
    assert compacted["data"][0]["b64_json"].startswith("[omitted inline data: base64, ")


def test_text_short_strings_and_remote_urls_are_kept_as_is() -> None:
    prose = "word " * 2000
    content = {
        "prompt": prose,
        "short": "data:image/png;base64,iVBORw0KGgo=",
        "url": "https://example.com/" + "a" * 2000 + "?x=1",
        "n": 3,
        "flag": None,
    }

    compacted, changed = compact_audit_content(content, max_bytes=_MAX)

    assert changed is False
    assert compacted is content


def test_payload_still_over_the_limit_is_replaced_whole() -> None:
    content = {"prompt": "word " * 4000}

    compacted, changed = compact_audit_content(content, max_bytes=4096)

    assert changed is True
    assert compacted["omitted"] == "payload_too_large"
    assert compacted["max_bytes"] == 4096
    assert compacted["approx_size_bytes"] > 4096


def test_images_are_dropped_before_the_size_limit_applies() -> None:
    content = {"messages": [{"content": [{"image_url": {"url": _image_data_url(100_000)}}]}]}

    compacted, changed = compact_audit_content(content, max_bytes=4096)

    assert changed is True
    assert "omitted" not in compacted
