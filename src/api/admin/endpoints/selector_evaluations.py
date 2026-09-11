from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.api.admin.selector_evaluation_route import SelectorEvaluationRoute
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.audit_service import require_audit_service
from src.services.selector_evaluation import (
    SelectorEvaluationReport,
    SelectorEvaluationRequest,
    evaluate_selector,
)

router = APIRouter(tags=["Admin Route Groups"], route_class=SelectorEvaluationRoute)


@router.post(
    "/ui/api/route-policy-evaluations",
    response_model=SelectorEvaluationReport,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
    responses={
        400: {"description": "Invalid fixture fields, bounds or labels"},
        401: {"description": "Authentication required"},
        403: {"description": "Permission denied"},
        408: {"description": "Upload timed out"},
        413: {"description": "Fixture exceeds 256 KiB"},
        503: {"description": "Required audit unavailable"},
    },
)
async def evaluate_route_selector(
    request: Request, response: Response, payload: SelectorEvaluationRequest
) -> SelectorEvaluationReport:
    require_audit_service(getattr(request.app.state, "audit_service", None))
    try:
        report = evaluate_selector(payload)
    except ValueError as exc:
        # Individually valid exact costs may overflow the aggregate money bound.
        # Never echo fixture values or convert an invalid report into zero spend.
        raise HTTPException(
            status_code=400, detail="Evaluation costs exceed report bounds"
        ) from exc
    await emit_admin_mutation_audit(
        request=request,
        action=AuditAction.ADMIN_ROUTE_SELECTOR_EVALUATE,
        resource_type="route_selector_evaluation",
        metadata={"basis": report.basis, "sample_count": report.sample_count},
        # No prompt, output, label, classifier topology or fixture hash in audit.
        force_sync=True,
    )
    response.headers["Cache-Control"] = "no-store"
    return report
