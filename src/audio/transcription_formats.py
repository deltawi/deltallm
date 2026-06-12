from __future__ import annotations

from typing import Any


def render_srt(response_payload: dict[str, Any]) -> str:
    segments = response_payload.get("segments")
    if not isinstance(segments, list) or not segments:
        return str(response_payload.get("text") or "")

    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _format_timestamp(segment.get("start"), decimal_separator=",")
        end = _format_timestamp(segment.get("end"), decimal_separator=",")
        lines.extend([str(index), f"{start} --> {end}", text, ""])
    return "\n".join(lines).strip()


def render_vtt(response_payload: dict[str, Any]) -> str:
    segments = response_payload.get("segments")
    if not isinstance(segments, list) or not segments:
        transcript_text = str(response_payload.get("text") or "")
        return f"WEBVTT\n\n{transcript_text}".strip()

    lines = ["WEBVTT", ""]
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _format_timestamp(segment.get("start"), decimal_separator=".")
        end = _format_timestamp(segment.get("end"), decimal_separator=".")
        lines.extend([f"{start} --> {end}", text, ""])
    return "\n".join(lines).strip()


def _format_timestamp(value: Any, *, decimal_separator: str) -> str:
    total_seconds = max(0.0, float(value or 0))
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))

    if milliseconds == 1000:
        milliseconds = 0
        seconds += 1
    if seconds == 60:
        seconds = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        hours += 1

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal_separator}{milliseconds:03d}"
