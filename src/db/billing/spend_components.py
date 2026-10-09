from typing import Literal


def external_request_sql(*, alias: Literal["", "s"] = "") -> str:
    """One definition for external-operation counts in existing admin reports."""
    if alias not in ("", "s"):
        raise ValueError("invalid spend table alias")
    column = f"{alias}.call_type" if alias else "call_type"
    return f"{column} IS DISTINCT FROM 'model_router_selector'"
