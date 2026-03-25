from src.api.admin.endpoints.auth_ui import router as auth_ui_router
from src.api.admin.endpoints.audit import router as audit_router
from src.api.admin.endpoints.batches import router as batches_router
from src.api.admin.endpoints.callable_targets import router as callable_targets_router
from src.api.admin.endpoints.config import router as config_router
from src.api.admin.endpoints.email import router as email_router
from src.api.admin.endpoints.email_feedback import router as email_feedback_router
from src.api.admin.endpoints.guardrails import router as guardrails_router
from src.api.admin.endpoints.invitations import router as invitations_router
from src.api.admin.endpoints.keys import router as keys_router
from src.api.admin.endpoints.mcp import router as mcp_router
from src.api.admin.endpoints.models import router as models_router
from src.api.admin.endpoints.organizations import router as organizations_router
from src.api.admin.endpoints.prompt_registry import router as prompt_registry_router
from src.api.admin.endpoints.rbac import router as rbac_router
from src.api.admin.endpoints.route_groups import router as route_groups_router
from src.api.admin.endpoints.service_accounts import router as service_accounts_router
from src.api.admin.endpoints.spend import router as spend_router
from src.api.admin.endpoints.teams import router as teams_router
from src.api.admin.endpoints.users import router as users_router

__all__ = [
    "audit_router",
    "auth_ui_router",
    "batches_router",
    "callable_targets_router",
    "config_router",
    "email_router",
    "email_feedback_router",
    "guardrails_router",
    "invitations_router",
    "keys_router",
    "mcp_router",
    "models_router",
    "organizations_router",
    "prompt_registry_router",
    "rbac_router",
    "route_groups_router",
    "service_accounts_router",
    "spend_router",
    "teams_router",
    "users_router",
]
