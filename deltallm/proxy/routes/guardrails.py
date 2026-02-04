"""Guardrails management routes.

This module provides API endpoints for:
- Managing content policies
- Testing content against policies
- Viewing guardrails logs
"""

import logging
from typing import Annotated, Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.session import get_db_session
from deltallm.db.models import User, Organization
from deltallm.proxy.dependencies import require_user
from deltallm.guardrails.manager import GuardrailsManager, GuardrailsResult
from deltallm.guardrails.policies import (
    ContentPolicy,
    PolicyAction,
    DEFAULT_POLICY,
    STRICT_POLICY,
    PERMISSIVE_POLICY,
    RESEARCH_POLICY,
)
from deltallm.rbac.exceptions import PermissionDeniedError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["guardrails"])


# ========== Schemas ==========

class PolicyResponse(BaseModel):
    """Content policy response."""
    id: str
    name: str
    description: str
    blocked_topics: List[str]
    allowed_topics: List[str]
    enable_pii_filter: bool
    enable_toxicity_filter: bool
    enable_injection_filter: bool
    pii_action: str
    toxicity_action: str
    injection_action: str
    custom_blocked_patterns: List[str]
    violation_action: str
    alert_on_violation: bool


class PolicyCreateRequest(BaseModel):
    """Request to create a policy."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    blocked_topics: List[str] = Field(default_factory=list)
    allowed_topics: List[str] = Field(default_factory=list)
    enable_pii_filter: bool = True
    enable_toxicity_filter: bool = True
    enable_injection_filter: bool = True
    pii_action: str = "redact"
    toxicity_action: str = "block"
    injection_action: str = "block"
    custom_blocked_patterns: List[str] = Field(default_factory=list)
    violation_action: str = "block"
    alert_on_violation: bool = True


class PolicyUpdateRequest(BaseModel):
    """Request to update a policy."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    blocked_topics: Optional[List[str]] = None
    allowed_topics: Optional[List[str]] = None
    enable_pii_filter: Optional[bool] = None
    enable_toxicity_filter: Optional[bool] = None
    enable_injection_filter: Optional[bool] = None
    pii_action: Optional[str] = None
    toxicity_action: Optional[str] = None
    injection_action: Optional[str] = None
    custom_blocked_patterns: Optional[List[str]] = None
    violation_action: Optional[str] = None
    alert_on_violation: Optional[bool] = None


class ContentCheckRequest(BaseModel):
    """Request to check content."""
    content: str = Field(..., min_length=1)
    policy_id: Optional[str] = None


class ContentCheckResponse(BaseModel):
    """Response from content check."""
    allowed: bool
    action: str
    message: Optional[str] = None
    filtered_content: Optional[str] = None
    violations: List[dict] = []


class GuardrailsStatusResponse(BaseModel):
    """Guardrails status for an organization."""
    org_id: str
    active_policy_id: Optional[str] = None
    active_policy_name: Optional[str] = None
    policies_available: List[str]
    total_requests_checked: int = 0
    total_violations: int = 0


# ========== Predefined Policies ==========

PREDEFINED_POLICIES = {
    "default": DEFAULT_POLICY,
    "strict": STRICT_POLICY,
    "permissive": PERMISSIVE_POLICY,
    "research": RESEARCH_POLICY,
}


# ========== Routes ==========

@router.get("/guardrails/policies", response_model=List[PolicyResponse])
async def list_policies(
    current_user: Annotated[User, Depends(require_user)],
):
    """List available guardrails policies."""
    policies = []
    
    for policy_id, policy in PREDEFINED_POLICIES.items():
        policies.append(PolicyResponse(
            id=policy_id,
            name=policy.name,
            description=policy.description,
            blocked_topics=list(policy.blocked_topics),
            allowed_topics=list(policy.allowed_topics),
            enable_pii_filter=policy.enable_pii_filter,
            enable_toxicity_filter=policy.enable_toxicity_filter,
            enable_injection_filter=policy.enable_injection_filter,
            pii_action=policy.pii_action,
            toxicity_action=policy.toxicity_action,
            injection_action=policy.injection_action,
            custom_blocked_patterns=policy.custom_blocked_patterns,
            violation_action=policy.violation_action.value,
            alert_on_violation=policy.alert_on_violation,
        ))
    
    return policies


@router.get("/guardrails/policies/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: str,
    current_user: Annotated[User, Depends(require_user)],
):
    """Get a specific policy."""
    if policy_id not in PREDEFINED_POLICIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found",
        )
    
    policy = PREDEFINED_POLICIES[policy_id]
    return PolicyResponse(
        id=policy_id,
        name=policy.name,
        description=policy.description,
        blocked_topics=list(policy.blocked_topics),
        allowed_topics=list(policy.allowed_topics),
        enable_pii_filter=policy.enable_pii_filter,
        enable_toxicity_filter=policy.enable_toxicity_filter,
        enable_injection_filter=policy.enable_injection_filter,
        pii_action=policy.pii_action,
        toxicity_action=policy.toxicity_action,
        injection_action=policy.injection_action,
        custom_blocked_patterns=policy.custom_blocked_patterns,
        violation_action=policy.violation_action.value,
        alert_on_violation=policy.alert_on_violation,
    )


@router.post("/guardrails/check", response_model=ContentCheckResponse)
async def check_content(
    data: ContentCheckRequest,
    current_user: Annotated[User, Depends(require_user)],
):
    """Check content against guardrails policies.
    
    This endpoint allows testing content without making an actual
    LLM request.
    """
    # Get policy
    policy = PREDEFINED_POLICIES.get(data.policy_id, DEFAULT_POLICY)
    
    # Create manager and check
    manager = GuardrailsManager(policy=policy)
    result = manager.check_prompt(data.content)
    
    return ContentCheckResponse(
        allowed=result.allowed,
        action=result.action,
        message=result.message,
        filtered_content=result.filtered_content,
        violations=[
            {
                "policy_id": v.policy_id,
                "policy_name": v.policy_name,
                "severity": v.severity,
                "message": v.message,
            }
            for v in result.violations
        ],
    )


@router.get("/guardrails/status")
async def get_guardrails_status(
    org_id: Optional[UUID] = None,
    current_user: Annotated[User, Depends(require_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
):
    """Get guardrails status for an organization.
    
    Returns information about active policies and statistics.
    """
    # In a real implementation, this would query the database
    # for the org's active policy and statistics
    
    return GuardrailsStatusResponse(
        org_id=str(org_id) if org_id else "global",
        active_policy_id="default",
        active_policy_name="Default Policy",
        policies_available=list(PREDEFINED_POLICIES.keys()),
        total_requests_checked=0,
        total_violations=0,
    )


@router.post("/guardrails/org/{org_id}/policy/{policy_id}")
async def set_org_policy(
    org_id: UUID,
    policy_id: str,
    current_user: Annotated[User, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
):
    """Set the active guardrails policy for an organization.
    
    Requires org:manage_policy permission.
    """
    if policy_id not in PREDEFINED_POLICIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found",
        )
    
    # Check permission
    try:
        from deltallm.rbac.manager import RBACManager
        rbac = RBACManager(db)
        await rbac.require_permission(
            current_user.id, org_id, "org", "manage_policy"
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"Error checking permission in set_org_policy: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission check failed"
        )
    
    # Verify org exists
    from sqlalchemy import select
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    
    # Update org settings with new policy
    # In a real implementation, this would persist the policy setting
    policy = PREDEFINED_POLICIES[policy_id]
    
    # Log the change (non-critical - continue even if audit logging fails)
    try:
        from deltallm.rbac.audit import AuditLogger
        audit = AuditLogger(db, current_user.id)
        await audit.log(
            action="guardrails:set_policy",
            resource_type="organization",
            resource_id=str(org_id),
            org_id=org_id,
            changes={
                "new_policy_id": policy_id,
                "new_policy_name": policy.name,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to log guardrails policy change audit: {e}")
    
    return {
        "success": True,
        "org_id": str(org_id),
        "policy_id": policy_id,
        "policy_name": policy.name,
    }
