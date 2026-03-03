from src.auth.roles import ORG_ROLE_PERMISSIONS, OrganizationRole, Permission


def test_audit_read_permission_assigned_to_org_admin_and_owner() -> None:
    assert Permission.AUDIT_READ in ORG_ROLE_PERMISSIONS[OrganizationRole.ADMIN]
    assert Permission.AUDIT_READ in ORG_ROLE_PERMISSIONS[OrganizationRole.OWNER]
    assert Permission.AUDIT_READ not in ORG_ROLE_PERMISSIONS[OrganizationRole.AUDITOR]
    assert Permission.AUDIT_READ not in ORG_ROLE_PERMISSIONS[OrganizationRole.BILLING]
    assert Permission.AUDIT_READ not in ORG_ROLE_PERMISSIONS[OrganizationRole.MEMBER]
