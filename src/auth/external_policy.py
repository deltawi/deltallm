from __future__ import annotations

from src.auth.roles import Permission

EXTERNAL_SESSION_PREFIX = "psk_ext1_"
CUSTOMER_PERMISSION_CEILING = frozenset(
    {
        Permission.ORG_READ,
        Permission.TEAM_READ,
        Permission.SPEND_READ_SELF,
        Permission.KEY_READ,
        Permission.KEY_CREATE_SELF,
        Permission.KEY_REVOKE,
    }
)
