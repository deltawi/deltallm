from fastapi import APIRouter

from src.api.admin.endpoints import (
    audit_router,
    auth_ui_router,
    batches_router,
    callable_targets_router,
    config_router,
    guardrails_router,
    keys_router,
    mcp_router,
    models_router,
    organizations_router,
    prompt_registry_router,
    rbac_router,
    route_groups_router,
    service_accounts_router,
    spend_router,
    teams_router,
    users_router,
)
from src.ui.routes import ui_router as legacy_ui_router

admin_router = APIRouter()

# Keep explicit order for predictable route registration.
admin_router.include_router(auth_ui_router)
admin_router.include_router(keys_router)
admin_router.include_router(mcp_router)
admin_router.include_router(callable_targets_router)
admin_router.include_router(models_router)
admin_router.include_router(service_accounts_router)
admin_router.include_router(teams_router)
admin_router.include_router(users_router)
admin_router.include_router(organizations_router)
admin_router.include_router(batches_router)
admin_router.include_router(rbac_router)
admin_router.include_router(guardrails_router)
admin_router.include_router(route_groups_router)
admin_router.include_router(prompt_registry_router)
admin_router.include_router(config_router)
admin_router.include_router(audit_router)
admin_router.include_router(spend_router)

# Include remaining UI endpoints not yet split (static files only).
admin_router.include_router(legacy_ui_router)

__all__ = ["admin_router"]
