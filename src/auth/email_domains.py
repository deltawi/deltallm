"""Email domain parsing shared by SSO sign-in rules and self-registration."""

from __future__ import annotations

MAX_EMAIL_DOMAINS = 200


def email_domain(email: str) -> str | None:
    """Return the lower-case domain of an email address, or `None` if it has no domain."""

    local_part, separator, domain = str(email or "").strip().lower().rpartition("@")
    if not separator or not local_part or not domain:
        return None
    return domain


def normalize_email_domains(values: list[str], *, field: str) -> list[str]:
    """Lower-case, deduplicate, and validate bare email domains such as `example.com`."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_domain in values:
        domain = str(raw_domain or "").strip().lower()
        if not domain:
            continue
        if (
            "@" in domain
            or "/" in domain
            or "\\" in domain
            or domain.startswith(".")
            or domain.endswith(".")
        ):
            raise ValueError(f"{field} entries must be bare email domains")
        if domain not in seen:
            normalized.append(domain)
            seen.add(domain)
    return normalized
