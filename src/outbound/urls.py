from urllib.parse import urlsplit, urlunsplit

MAX_OUTBOUND_URL_LENGTH = 2_048


def normalize_outbound_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("outbound url is required")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("outbound url must contain valid UTF-8 text") from exc
    if len(normalized) > MAX_OUTBOUND_URL_LENGTH:
        raise ValueError(f"outbound url must be at most {MAX_OUTBOUND_URL_LENGTH} characters")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in normalized):
        raise ValueError("outbound url must not contain whitespace or control characters")
    if "\\" in normalized:
        raise ValueError("outbound url must not contain backslashes")

    try:
        parsed = urlsplit(normalized)
        port = parsed.port
        if port == 0:
            raise ValueError("outbound url port is invalid")
    except ValueError as exc:
        raise ValueError("outbound url is invalid") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("outbound url scheme must be https (or http when explicitly enabled)")
    if not parsed.hostname:
        raise ValueError("outbound url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("outbound url must not include user information")
    if "#" in normalized:
        raise ValueError("outbound url must not include a fragment")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("outbound url hostname is invalid") from exc
    if not hostname:
        raise ValueError("outbound url must include a hostname")
    if ":" not in hostname:
        hostname = hostname.rstrip(".")
        if len(hostname) > 253:
            raise ValueError("outbound url hostname is invalid")
        labels = hostname.split(".")
        if not hostname or any(
            len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(
                character.isascii() and (character.isalnum() or character == "-")
                for character in label
            )
            for label in labels
        ):
            raise ValueError("outbound url hostname is invalid")

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        rendered_host = f"{rendered_host}:{port}"

    canonical_url = urlunsplit((scheme, rendered_host, parsed.path or "/", parsed.query, ""))
    if len(canonical_url) > MAX_OUTBOUND_URL_LENGTH:
        raise ValueError(f"outbound url must be at most {MAX_OUTBOUND_URL_LENGTH} characters")
    return canonical_url
