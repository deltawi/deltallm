from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

# Matched against field names. Plain "key"/"token" would also match max_tokens and
# input_cost_per_token, so only credential-shaped names count.
SECRET_FIELD_MARKERS = ("api_key", "access_key", "secret", "password", "session_token")
SECRET_FIELD_NAMES = frozenset({"key", "token", "master_key", "auth_token"})
ENV_REFERENCE_PREFIX = "os.environ/"


class Severity(str, Enum):
    """How much attention a finding needs before the converted file goes live."""

    ACTION = "action"  # The operator must do something before or after the first start.
    SKIPPED = "skipped"  # Input was not converted; the behavior is missing in DeltaLLM.
    CHANGED = "changed"  # Input was converted with a different value or meaning.
    INFO = "info"


_SEVERITY_TITLES = {
    Severity.ACTION: "Action needed",
    Severity.SKIPPED: "Not imported",
    Severity.CHANGED: "Changed during import",
    Severity.INFO: "Notes",
}


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    path: str
    message: str


@dataclass(slots=True)
class ImportReport:
    """Ordered, de-duplicated findings. Messages never contain configuration values."""

    findings: list[Finding] = field(default_factory=list)
    imported_deployments: int = 0
    skipped_deployments: int = 0

    def add(self, severity: Severity, path: str, message: str) -> None:
        finding = Finding(severity=severity, path=path, message=message)
        if finding not in self.findings:
            self.findings.append(finding)

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity is severity]

    @property
    def has_skipped(self) -> bool:
        return self.skipped_deployments > 0 or bool(self.by_severity(Severity.SKIPPED))

    def summary_line(self) -> str:
        return (
            f"Imported {self.imported_deployments} deployment(s), "
            f"skipped {self.skipped_deployments}. "
            f"{len(self.by_severity(Severity.ACTION))} action(s) needed, "
            f"{len(self.by_severity(Severity.SKIPPED))} setting(s) not imported, "
            f"{len(self.by_severity(Severity.CHANGED))} changed."
        )

    def render_text(self) -> str:
        lines = [self.summary_line()]
        for severity in Severity:
            items = self.by_severity(severity)
            if not items:
                continue
            lines.append("")
            lines.append(f"{_SEVERITY_TITLES[severity]}:")
            lines.extend(f"  - {item.path}: {item.message}" for item in items)
        return "\n".join(lines) + "\n"

    def render_markdown(self, *, source_name: str) -> str:
        lines = [
            "# LiteLLM import report",
            "",
            f"Source: `{source_name}`",
            "",
            self.summary_line(),
        ]
        for severity in Severity:
            items = self.by_severity(severity)
            if not items:
                continue
            lines.extend(["", f"## {_SEVERITY_TITLES[severity]}", ""])
            lines.extend(f"- `{item.path}`: {item.message}" for item in items)
        return "\n".join(lines) + "\n"


def is_secret_field(name: object) -> bool:
    lowered = str(name).lower()
    return lowered in SECRET_FIELD_NAMES or any(
        marker in lowered for marker in SECRET_FIELD_MARKERS
    )


def is_env_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ENV_REFERENCE_PREFIX)


def flag_literal_secrets(
    report: ImportReport, path: str, values: Iterable[tuple[str, object]]
) -> None:
    """Flag secret-looking fields that hold a literal value instead of an env reference."""

    for name, value in values:
        if not is_secret_field(name) or value in (None, ""):
            continue
        if isinstance(value, str) and not is_env_reference(value):
            report.add(
                Severity.ACTION,
                f"{path}.{name}",
                "holds a literal secret. Move it to an environment variable and use "
                f"`{ENV_REFERENCE_PREFIX}NAME`.",
            )
