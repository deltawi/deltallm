from __future__ import annotations

from src.audit.actions import AuditAction

# Category of an audit action, read from its name prefix. Data-plane actions
# such as CHAT_COMPLETION_REQUEST are not all in the enum, so "api" is
# everything that does not match another category.
AUDIT_CATEGORY_PREFIXES: dict[str, tuple[str, ...]] = {
    "admin": ("ADMIN_", "MANAGED_ASSET_"),
    "auth": ("AUTH_",),
    "mcp": ("MCP_",),
    "spend": ("GLOBAL_SPEND_", "SPEND_"),
    "system": ("SYSTEM_", "EXTERNAL_AUTH_", "EMAIL_DELIVERY_", "BATCH_WEBHOOK_"),
}
AUDIT_CATEGORIES: tuple[str, ...] = ("admin", "auth", "api", "mcp", "spend", "system")

# Request actions written outside the enum (src/chat/audit.py).
_EXTRA_DATA_PLANE_ACTIONS = ("CHAT_COMPLETION_REQUEST", "COMPLETION_REQUEST", "RESPONSES_REQUEST")


def audit_action_category(action: str) -> str:
    for category, prefixes in AUDIT_CATEGORY_PREFIXES.items():
        if action.startswith(prefixes):
            return category
    return "api"


def audit_category_clause(category: str, column: str = "action") -> str:
    """SQL condition for one category. Prefixes are constants, never user input."""
    if category == "api":
        prefixes = [prefix for group in AUDIT_CATEGORY_PREFIXES.values() for prefix in group]
        return "NOT (" + " OR ".join(f"starts_with({column}, '{prefix}')" for prefix in prefixes) + ")"
    prefixes = AUDIT_CATEGORY_PREFIXES[category]
    return "(" + " OR ".join(f"starts_with({column}, '{prefix}')" for prefix in prefixes) + ")"


def known_audit_actions() -> list[dict[str, str]]:
    actions = {item.value for item in AuditAction} | set(_EXTRA_DATA_PLANE_ACTIONS)
    return [{"action": action, "category": audit_action_category(action)} for action in sorted(actions)]
