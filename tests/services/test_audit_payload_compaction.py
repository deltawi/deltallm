from __future__ import annotations

import base64
from hashlib import sha256

from src.services.audit.payload_compaction import compact_audit_content

_MAX = 1_048_576
# A limit no test value reaches.
_NO_LIMIT = 1 << 40


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


def _reference_placeholder(value: str) -> str | None:
    """The placeholder as one whole-string scan and hash computes it."""

    import re

    data_url = re.compile(r"data:([\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[^;,]*)*;base64,", re.ASCII)
    base64_chars = re.compile(r"[A-Za-z0-9+/=_\-\r\n]+", re.ASCII)
    if len(value) < 1024:
        return None
    match = data_url.match(value)
    if match is not None:
        media_type = match.group(1) or "application/octet-stream"
        if base64_chars.fullmatch(value, match.end()) is None:
            return None
    elif base64_chars.fullmatch(value) is not None:
        media_type = "base64"
    else:
        return None
    digest = sha256(value.encode()).hexdigest()
    return f"[omitted inline data: {media_type}, {len(value)} chars, sha256={digest}]"


def test_long_strings_are_scanned_and_hashed_in_slices_with_the_same_result(monkeypatch) -> None:
    from src.services.audit import payload_compaction

    monkeypatch.setattr(payload_compaction, "_SCAN_CHUNK_CHARS", 7)
    data = base64.b64encode(bytes(range(256)) * 8).decode()
    cases = [
        data,
        data + "!",
        "!" + data,
        data[:500] + " " + data[500:],
        "data:image/png;base64," + data,
        "data:image/png;base64," + data + "é",
        "data:application/pdf;name=café;base64," + data,
        "data:image/png;base64," + "x" * 1024,
        "data:image/png;base64,",
        "é" * 2048,
        "A" * 1023,
        "A" * 1024,
    ]
    for value in cases:
        assert payload_compaction._inline_data_placeholder(value) == _reference_placeholder(value)


def test_a_data_url_header_is_read_only_at_the_start_of_the_string(monkeypatch) -> None:
    from src.services.audit import payload_compaction

    real = payload_compaction._DATA_URL
    calls: list[tuple[int, int]] = []

    class _Spy:
        def match(self, value, pos=0, endpos=None):  # noqa: ANN001, ANN201
            calls.append((pos, endpos))
            return real.match(value, pos, endpos)

    monkeypatch.setattr(payload_compaction, "_DATA_URL", _Spy())
    data = base64.b64encode(b"\x00" * 3000).decode()
    named = "data:application/pdf;name=report.pdf;base64," + data
    assert compact_audit_content({"file": named}, max_bytes=_MAX)[0]["file"].startswith(
        "[omitted inline data: application/pdf, "
    )
    # A header that does not end early is plain text; the regex never reads past it.
    long_header = "data:" + "a" * 5000 + "!"
    long_parameter = "data:a/b;x=" + "y" * 5000
    for value in (long_header, long_parameter):
        assert compact_audit_content({"text": value}, max_bytes=_MAX) == ({"text": value}, False)
    assert calls and all(
        endpos == payload_compaction._DATA_URL_MAX_HEADER_CHARS for _, endpos in calls
    )


def test_non_ascii_text_is_counted_by_its_json_size(monkeypatch) -> None:
    import json

    from src.services.audit import payload_compaction

    samples = ["abc", "é" * 1000, "مرحبا بالعالم " * 50, "😀 a 中", "x y", "ü" + "a" * 3000]
    for chunk in (7, payload_compaction._SCAN_CHUNK_CHARS):
        monkeypatch.setattr(payload_compaction, "_SCAN_CHUNK_CHARS", chunk)
        for value in samples:
            assert payload_compaction._compact(value, stop=_NO_LIMIT)[1] == len(json.dumps(value))
        # A non-ASCII key is counted like its value would be.
        assert (
            payload_compaction._compact({"clé": 1}, stop=_NO_LIMIT)[1]
            - payload_compaction._compact({"cle": 1}, stop=_NO_LIMIT)[1]
            == 5
        )
    # The count stops once a string is surely over the limit.
    monkeypatch.setattr(payload_compaction, "_SCAN_CHUNK_CHARS", 10)
    partial = payload_compaction._text_size("ع" * 100, stop=30)
    assert 30 < partial < len(json.dumps("ع" * 100))
    # So an Arabic body over the payload limit becomes the placeholder, though its
    # length is far below it.
    arabic = {"messages": [{"role": "user", "content": "ع" * 200_000}]}
    compacted, changed = compact_audit_content(arabic, max_bytes=_MAX)
    assert changed is True
    assert compacted["omitted"] == "payload_too_large"


def test_floats_count_their_longest_text() -> None:
    import json

    from src.services.audit import payload_compaction

    for value in (-1.7976931348623157e308, 2.2250738585072014e-308, 0.1, 1e-300, -0.0):
        assert payload_compaction._compact(value, stop=_NO_LIMIT)[1] >= len(json.dumps(value))
    # 50,000 floats of 24 characters: over 1 MiB, so the body is replaced.
    floats = [-1.7976931348623157e308] * 50_000
    compacted, changed = compact_audit_content({"enum": floats}, max_bytes=_MAX)
    assert changed is True
    assert compacted["omitted"] == "payload_too_large"


def test_size_work_stops_once_the_whole_payload_is_over_its_limit(monkeypatch) -> None:
    """Many strings that are each small: the limit applies to the whole traversal."""

    from src.services.audit import payload_compaction

    encoded: list[int] = []
    real_encode = payload_compaction.encode_basestring_ascii

    def counting_encode(value: str) -> str:
        encoded.append(len(value))
        return real_encode(value)

    monkeypatch.setattr(payload_compaction, "encode_basestring_ascii", counting_encode)
    # A tool enum of 10,000 Arabic strings: each is about 9 KB of JSON, so the
    # payload is over 1 MiB after about 117 of them.
    word = "ع" * 1500
    tool = {"type": "function", "function": {"parameters": {"enum": [word] * 10_000}}}
    compacted, changed = compact_audit_content({"tools": [tool]}, max_bytes=_MAX)
    assert changed is True
    assert compacted["omitted"] == "payload_too_large"
    assert compacted["approx_size_bytes"] > _MAX
    assert len(encoded) <= _MAX // (6 * len(word)) + 1

    # One large string stops after the slice that takes it over the limit.
    encoded.clear()
    compacted, _ = compact_audit_content({"text": "ع" * 2_000_000}, max_bytes=_MAX)
    assert compacted["omitted"] == "payload_too_large"
    assert encoded == [payload_compaction._SCAN_CHUNK_CHARS]

    # Keys count against the same limit.
    encoded.clear()
    keyed = {f"{word}{index}": 1 for index in range(10_000)}
    compacted, _ = compact_audit_content(keyed, max_bytes=_MAX)
    assert compacted["omitted"] == "payload_too_large"
    assert len(encoded) <= _MAX // (6 * len(word)) + 1


def test_inline_data_after_the_limit_is_not_scanned_or_hashed(monkeypatch) -> None:
    from src.services.audit import payload_compaction

    hashed: list[int] = []
    real_hash = payload_compaction._sha256

    def counting_hash(value: str) -> str:
        hashed.append(len(value))
        return real_hash(value)

    monkeypatch.setattr(payload_compaction, "_sha256", counting_hash)
    data = base64.b64encode(b"\x00" * 1500).decode()
    placeholder = payload_compaction._inline_data_placeholder(data)
    assert placeholder is not None
    hashed.clear()
    content = {"images": [data] * 10_000}
    compacted, _ = compact_audit_content(content, max_bytes=10_000)
    assert compacted["omitted"] == "payload_too_large"
    # Each image becomes a placeholder of about 120 characters: the payload is over
    # 10,000 bytes after about 80 of them, and the rest are never read.
    assert len(hashed) <= 10_000 // (len(placeholder) + 3) + 1

    # Under the limit, every image is still replaced.
    hashed.clear()
    compacted, changed = compact_audit_content({"images": [data] * 50}, max_bytes=_MAX)
    assert changed is True
    assert len(hashed) == 50
    assert compacted["images"] == [placeholder] * 50


def test_the_limit_applies_to_the_whole_payload_at_its_edge() -> None:
    """A payload of exactly the limit is kept; one byte more is replaced."""

    from src.services.audit import payload_compaction

    content = {"a": ["é" * 10, "x" * 20], "b": {"c": "ü" * 5}}
    _, size, _ = payload_compaction._compact(content, stop=_NO_LIMIT)
    assert compact_audit_content(content, max_bytes=size) == (content, False)
    compacted, changed = compact_audit_content(content, max_bytes=size - 1)
    assert changed is True
    assert compacted["omitted"] == "payload_too_large"
    # A count that stops early is over its limit exactly when the whole count is.
    for stop in range(size):
        assert payload_compaction._compact(content, stop=stop)[1] > stop
    for stop in (size, size + 1, _MAX):
        assert payload_compaction._compact(content, stop=stop)[1] == size
