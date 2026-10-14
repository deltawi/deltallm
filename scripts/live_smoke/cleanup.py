"""Remove the suite organization, test user, and tier (the --cleanup option).

Organization deletion is the gateway's own durable job. It removes the team,
memberships, keys, and tier assignment. The tier can go only after the job
completes, because an assignment keeps it in use. When the gateway has
organization deletion turned off, the suite removes the team and the tier
assignment and leaves the empty organization.
"""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic, sleep
from uuid import uuid4

from scripts.live_smoke.config import ResourceNames
from scripts.live_smoke.gateway_client import CALL_ERRORS, GatewayClient, describe_error
from scripts.live_smoke.results import CheckResult, Status
from scripts.live_smoke.provisioning import delete_suite_keys, find_suite_organizations
from scripts.live_smoke.tier_sync import find_tier

DELETION_WAIT_SECONDS = 120.0
DELETION_POLL_SECONDS = 2.0


def _delete_organization(
    admin: GatewayClient,
    names: ResourceNames,
    org_id: str,
    *,
    clock: Callable[[], float],
    wait: Callable[[float], None],
) -> CheckResult:
    label = f"organization {org_id}"
    base = f"/ui/api/organizations/{org_id}"
    response = admin.call("GET", f"{base}/deletion-plan", "read deletion plan", expect=(200, 404))
    if response.status_code == 404:
        return CheckResult("cleanup", label, Status.PASS, "not present")
    plan = response.json()
    confirmation = plan.get("organization_name") or org_id
    job_id = plan.get("deletion_job_id")
    if not job_id:
        if not plan.get("requests_enabled", True):
            return _detach_organization(admin, names, org_id)
        if not plan.get("can_request"):
            blockers = ", ".join(plan.get("blocking_dependencies") or []) or "not allowed"
            return CheckResult("cleanup", label, Status.FAIL, f"blocked: {blockers}")
        job = admin.json(
            "POST",
            f"{base}/deletion-requests",
            "request organization deletion",
            expect=(202,),
            headers={"Idempotency-Key": f"live-smoke-delete-{uuid4()}"},
            json={
                "confirmation_name": confirmation,
                "plan_token": plan["plan_token"],
                "acknowledge_running_work_cancellation": True,
            },
        )
        job_id = job["deletion_job_id"]
    admin.call(
        "POST",
        f"{base}/deletion-requests/{job_id}/expedite",
        "expedite organization deletion",
        expect=(202, 409),
        headers={"Idempotency-Key": f"live-smoke-expedite-{job_id}"},
        json={
            "confirmation_name": confirmation,
            "acknowledge_immediate_irreversible_deletion": True,
        },
    )
    deadline = clock() + DELETION_WAIT_SECONDS
    while True:
        job = admin.json("GET", f"{base}/deletion-requests/{job_id}", "read deletion job")
        status = job.get("status")
        if status == "completed":
            return CheckResult("cleanup", label, Status.PASS, "deleted")
        if status == "failed" or clock() >= deadline:
            detail = job.get("last_error_code") or f"still {status} ({job.get('phase')})"
            return CheckResult("cleanup", label, Status.WARN, f"deletion job {job_id}: {detail}")
        wait(DELETION_POLL_SECONDS)


def _detach_organization(admin: GatewayClient, names: ResourceNames, org_id: str) -> CheckResult:
    """Fallback when organization deletion is turned off on the gateway.

    Remove the team and the tier assignment, so the test user and the tier can go.
    The empty organization stays, and the next run uses it again.
    """

    team_id = names.team_id_for(org_id)
    admin.call("DELETE", f"/ui/api/teams/{team_id}", "delete suite team", expect=(200, 404))
    base = f"/ui/api/organizations/{org_id}/tier-assignments"
    for assignment in admin.json("GET", base, "list organization tier assignments")["data"]:
        admin.call(
            "DELETE",
            f"{base}/{assignment['assignment_id']}",
            "remove organization tier assignment",
            expect=(200, 404),
        )
    return CheckResult(
        "cleanup",
        f"organization {org_id}",
        Status.WARN,
        "organization deletion is turned off on this gateway "
        "(organization_deletion_requests_enabled); removed its team and tier assignment, "
        "the empty organization stays",
    )


def _delete_user(admin: GatewayClient, names: ResourceNames) -> CheckResult:
    accounts = admin.json("GET", "/ui/api/rbac/accounts", "list accounts")
    match = [item for item in accounts if item.get("email") == names.user_email]
    if not match:
        return CheckResult("cleanup", "test user", Status.PASS, "not present")
    response = admin.call(
        "DELETE",
        f"/ui/api/rbac/accounts/{match[0]['account_id']}",
        "delete test user",
        expect=(200, 404, 409),
    )
    if response.status_code == 409:
        return CheckResult(
            "cleanup", "test user", Status.WARN, "still in use; run --cleanup-only again later"
        )
    return CheckResult("cleanup", "test user", Status.PASS, "deleted")


def _delete_tier(admin: GatewayClient, names: ResourceNames) -> CheckResult:
    tier = find_tier(admin, names.tier_key)
    if tier is None:
        return CheckResult("cleanup", "tier", Status.PASS, "not present")
    response = admin.call(
        "DELETE", f"/ui/api/tiers/{tier['tier_id']}", "delete suite tier", expect=(200, 409)
    )
    if response.status_code == 409:
        return CheckResult(
            "cleanup", "tier", Status.WARN, "still in use; run --cleanup-only again later"
        )
    return CheckResult("cleanup", "tier", Status.PASS, "deleted")


def _clean_organizations(
    admin: GatewayClient,
    names: ResourceNames,
    *,
    clock: Callable[[], float],
    wait: Callable[[float], None],
) -> list[CheckResult]:
    organizations = find_suite_organizations(admin, names)
    if not organizations:
        return [CheckResult("cleanup", "organization", Status.PASS, "not present")]
    results = []
    for org in organizations:
        org_id = str(org["organization_id"])
        try:
            if org.get("lifecycle_state") in (None, "active"):
                removed = delete_suite_keys(admin, names, names.team_id_for(org_id))
                results.append(
                    CheckResult(
                        "cleanup", f"test keys in {org_id}", Status.PASS, f"{removed} removed"
                    )
                )
            results.append(_delete_organization(admin, names, org_id, clock=clock, wait=wait))
        except CALL_ERRORS as exc:
            detail = describe_error(exc)
            results.append(CheckResult("cleanup", f"organization {org_id}", Status.FAIL, detail))
    return results


def cleanup_resources(
    admin: GatewayClient,
    names: ResourceNames,
    *,
    clock: Callable[[], float] = monotonic,
    wait: Callable[[float], None] = sleep,
) -> list[CheckResult]:
    """Delete every suite resource for this prefix. Each step reports its own result.

    A failed step does not stop the next steps, and no error escapes.
    """

    try:
        results = _clean_organizations(admin, names, clock=clock, wait=wait)
    except CALL_ERRORS as exc:
        results = [CheckResult("cleanup", "organization", Status.FAIL, describe_error(exc))]
    for label, step in (("test user", _delete_user), ("tier", _delete_tier)):
        try:
            results.append(step(admin, names))
        except CALL_ERRORS as exc:
            results.append(CheckResult("cleanup", label, Status.FAIL, describe_error(exc)))
    return results
