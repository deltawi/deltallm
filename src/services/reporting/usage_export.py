"""File formats for usage report and request log exports.

Exports use fixed column allowlists. They never include request metadata,
cache keys or prompt and response content.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

ReportExportFormat = Literal["csv", "json"]
LogExportFormat = Literal["csv", "jsonl"]

DEFAULT_EXPORT_ROWS = 10_000
MAX_EXPORT_ROWS = 50_000

REPORT_COLUMNS: tuple[str, ...] = (
    "dimension",
    "group_key",
    "name",
    "spend",
    "requests",
    "failed_requests",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
)

REQUEST_LOG_COLUMNS: tuple[str, ...] = (
    "id",
    "request_id",
    "start_time",
    "end_time",
    "status",
    "http_status_code",
    "error_type",
    "call_type",
    "model",
    "provider",
    "organization_id",
    "organization_name",
    "team_id",
    "team_alias",
    "api_key",
    "key_name",
    "user",
    "user_email",
    "end_user",
    "cache_hit",
    "spend",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_tokens_cached",
    "completion_tokens_cached",
    "deployment_model",
    "latency_ms",
    "duration_ms",
    "request_tags",
)

# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
}


@dataclass(frozen=True, slots=True)
class ExportFile:
    content: str
    media_type: str
    filename: str
    rows: int
    truncated: bool


def report_export_row(dimension: str, row: Mapping[str, Any], *, bucketed: bool) -> dict[str, Any]:
    """Map one usage report row to the export columns."""

    item: dict[str, Any] = {}
    if bucketed:
        item["date"] = str(row.get("bucket")) if row.get("bucket") is not None else None
    item.update(
        {
            "dimension": dimension,
            "group_key": row.get("group_key"),
            "name": row.get("display_name") or ("Unassigned" if row.get("is_unassigned") else None),
            "spend": float(row.get("total_spend") or 0),
            "requests": int(row.get("request_count") or 0),
            "failed_requests": int(row.get("failed_requests") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
        }
    )
    return item


def request_log_export_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the allowlisted request log columns."""

    return {column: row.get(column) for column in REQUEST_LOG_COLUMNS}


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, default=str, separators=(",", ":"))
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv(columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(column)) for column in columns])
    return buffer.getvalue()


def to_jsonl(rows: Iterable[Mapping[str, Any]]) -> str:
    lines = [json.dumps(row, default=str, separators=(",", ":")) for row in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def export_filename(stem: str, start: date | None, end: date | None, extension: str) -> str:
    """A file name with only safe characters, for example ``usage-model-2026-10-01-to-2026-10-28.csv``."""

    period = (
        f"{start.isoformat()}-to-{end.isoformat()}"
        if start and end
        else (f"to-{end.isoformat()}" if end else "all")
    )
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem)
    return f"{safe_stem}-{period}.{extension}"


def build_report_export(
    *,
    rows: Sequence[Mapping[str, Any]],
    dimension: str,
    interval: str | None,
    max_rows: int,
    export_format: ReportExportFormat,
    window: Mapping[str, Any],
    filename: str,
) -> ExportFile:
    truncated = len(rows) > max_rows
    items = [
        report_export_row(dimension, row, bucketed=interval is not None) for row in rows[:max_rows]
    ]
    if export_format == "csv":
        columns = (("date",) if interval else ()) + REPORT_COLUMNS
        content = to_csv(columns, items)
    else:
        content = json.dumps(
            {
                "group_by": dimension,
                "interval": interval,
                "window": dict(window),
                "truncated": truncated,
                "rows": items,
            },
            default=str,
        )
    return ExportFile(
        content=content,
        media_type=_MEDIA_TYPES[export_format],
        filename=filename,
        rows=len(items),
        truncated=truncated,
    )


def build_request_log_export(
    *,
    rows: Sequence[Mapping[str, Any]],
    max_rows: int,
    export_format: LogExportFormat,
    filename: str,
) -> ExportFile:
    truncated = len(rows) > max_rows
    items = [request_log_export_row(row) for row in rows[:max_rows]]
    content = to_csv(REQUEST_LOG_COLUMNS, items) if export_format == "csv" else to_jsonl(items)
    return ExportFile(
        content=content,
        media_type=_MEDIA_TYPES[export_format],
        filename=filename,
        rows=len(items),
        truncated=truncated,
    )
