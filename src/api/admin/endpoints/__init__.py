from src.api.admin.endpoints.audit import router as audit_router
from src.api.admin.endpoints.batches import router as batches_router
from src.api.admin.endpoints.config import router as config_router
from src.api.admin.endpoints.guardrails import router as guardrails_router
from src.api.admin.endpoints.keys import router as keys_router
from src.api.admin.endpoints.organizations import router as organizations_router
from src.api.admin.endpoints.rbac import router as rbac_router
from src.api.admin.endpoints.teams import router as teams_router
from src.api.admin.endpoints.users import router as users_router

__all__ = [
    "audit_router",
    "batches_router",
    "config_router",
    "guardrails_router",
    "keys_router",
    "organizations_router",
    "rbac_router",
    "teams_router",
    "users_router",
]
