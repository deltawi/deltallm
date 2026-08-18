from __future__ import annotations


_RECORD_SPECIFIC_PRISMA_CODES = {
    "P2000",
    "P2002",
    "P2003",
    "P2006",
    "P2011",
    "P2012",
    "P2013",
    "P2014",
    "P2020",
    "P2023",
}


def is_record_specific_database_error(exc: Exception) -> bool:
    """Distinguish bad-record failures from shared infrastructure failures."""

    if isinstance(exc, (TypeError, ValueError)):
        return True
    current: BaseException | None = exc
    while current is not None:
        codes = {
            str(code)
            for code in (
                getattr(current, "code", None),
                getattr(current, "sqlstate", None),
                getattr(current, "pgcode", None),
            )
            if code
        }
        metadata = getattr(current, "meta", None)
        if isinstance(metadata, dict):
            codes.update(
                str(code)
                for key, code in metadata.items()
                if code and str(key).lower() in {"code", "sqlstate", "pgcode"}
            )
        if any(
            code in _RECORD_SPECIFIC_PRISMA_CODES or code.startswith(("22", "23")) for code in codes
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
