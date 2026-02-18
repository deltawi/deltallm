from fastapi import APIRouter

from src.api.admin.endpoints import (
    config_router,
    guardrails_router,
    keys_router,
    organizations_router,
    teams_router,
    users_router,
)
from src.ui.routes import ui_router as legacy_ui_router

admin_router = APIRouter()

# Keep explicit order for predictable route registration.
admin_router.include_router(keys_router)
admin_router.include_router(users_router)
admin_router.include_router(teams_router)
admin_router.include_router(organizations_router)
admin_router.include_router(guardrails_router)
admin_router.include_router(config_router)

# Include remaining UI endpoints not yet split (models, spend/logs, auth, static files).
admin_router.include_router(legacy_ui_router)

__all__ = ["admin_router"]
