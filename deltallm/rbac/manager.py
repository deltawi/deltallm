"""RBAC Manager for permission and role management.

This module provides the RBACManager class which is the main interface
for checking permissions, assigning roles, and managing the RBAC system.
"""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import (
    OrgMember,
    Organization,
    Permission as PermissionModel,
    Role,
    RolePermission,
    Team,
    TeamMember,
    User,
)
from deltallm.rbac.exceptions import (
    DuplicateRoleError,
    OrganizationNotFoundError,
    PermissionDeniedError,
    RoleNotFoundError,
    SystemRoleModificationError,
    TeamNotFoundError,
    UserNotInOrganizationError,
)
from deltallm.rbac.permissions import Permission, PermissionChecker

if TYPE_CHECKING:
    from uuid import UUID


class RBACManager:
    """Manages role-based access control operations.
    
    This class provides methods for:
    - Checking user permissions
    - Assigning/revoking roles
    - Creating custom roles
    - Managing organization/team memberships
    
    Example:
        async with get_session() as session:
            rbac = RBACManager(session)
            
            # Check if user has permission
            has_perm = await rbac.check_permission(
                user_id, org_id, "api_key", "create"
            )
            
            # Assign role to user
            await rbac.assign_role(user_id, org_id, "org_admin")
    """
    
    def __init__(self, session: AsyncSession):
        """Initialize the RBAC manager.
        
        Args:
            session: SQLAlchemy async session.
        """
        self.session = session
        self._permission_cache: dict[str, list[str]] = {}
    
    # ========== Permission Checking ==========
    
    async def check_permission(
        self,
        user_id: "UUID",
        org_id: "UUID",
        resource: str,
        action: str,
        team_id: "UUID | None" = None,
    ) -> bool:
        """Check if a user has a specific permission.
        
        This method checks if the user has the required permission either
        at the organization level or at a specific team level.
        
        Args:
            user_id: The user's UUID.
            org_id: The organization's UUID.
            resource: The resource type (e.g., "api_key", "model").
            action: The action (e.g., "create", "read", "use").
            team_id: Optional team ID for team-level permission checks.
            
        Returns:
            True if the user has the permission, False otherwise.
        """
        # Superuser always has all permissions
        user = await self.session.get(User, user_id)
        if user and user.is_superuser:
            return True
        
        required = Permission(resource, action)
        
        # Get user's permissions
        user_permissions = await self.get_user_permissions(user_id, org_id, team_id)
        
        # Check if any of the user's permissions match the required permission
        for perm_str in user_permissions:
            if required.matches(perm_str):
                return True
        
        return False
    
    async def get_user_permissions(
        self,
        user_id: "UUID",
        org_id: "UUID",
        team_id: "UUID | None" = None,
    ) -> list[str]:
        """Get all permissions for a user in an organization/team.
        
        Args:
            user_id: The user's UUID.
            org_id: The organization's UUID.
            team_id: Optional team ID to get team-specific permissions.
            
        Returns:
            List of permission strings (e.g., ["api_key:create", "model:use"]).
        """
        # Check if user is in organization
        org_member = await self.session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user_id,
                OrgMember.org_id == org_id,
            )
        )
        org_member = org_member.scalar_one_or_none()
        
        if not org_member:
            return []
        
        permissions: set[str] = set()
        
        # Get organization-level role permissions
        org_role = await self.session.execute(
            select(Role)
            .join(OrgMember, OrgMember.role == Role.name)
            .where(
                OrgMember.user_id == user_id,
                OrgMember.org_id == org_id,
                Role.org_id.is_(None),  # System roles only
            )
        )
        org_role = org_role.scalar_one_or_none()
        
        if org_role:
            role_permissions = await self.session.execute(
                select(PermissionModel)
                .join(RolePermission)
                .where(RolePermission.role_id == org_role.id)
            )
            for perm in role_permissions.scalars():
                permissions.add(f"{perm.resource}:{perm.action}")
        
        # If team_id specified, also get team-level permissions
        if team_id:
            team_member = await self.session.execute(
                select(TeamMember).where(
                    TeamMember.user_id == user_id,
                    TeamMember.team_id == team_id,
                )
            )
            team_member = team_member.scalar_one_or_none()
            
            if team_member:
                team_role = await self.session.execute(
                    select(Role)
                    .where(
                        Role.name == team_member.role,
                        Role.org_id.is_(None),  # System roles
                    )
                )
                team_role = team_role.scalar_one_or_none()
                
                if team_role:
                    role_permissions = await self.session.execute(
                        select(PermissionModel)
                        .join(RolePermission)
                        .where(RolePermission.role_id == team_role.id)
                    )
                    for perm in role_permissions.scalars():
                        permissions.add(f"{perm.resource}:{perm.action}")
        
        return list(permissions)
    
    async def require_permission(
        self,
        user_id: "UUID",
        org_id: "UUID",
        resource: str,
        action: str,
        team_id: "UUID | None" = None,
    ) -> None:
        """Require a permission, raising an exception if not granted.
        
        Args:
            user_id: The user's UUID.
            org_id: The organization's UUID.
            resource: The resource type.
            action: The action.
            team_id: Optional team ID.
            
        Raises:
            PermissionDeniedError: If the user doesn't have the permission.
        """
        if not await self.check_permission(user_id, org_id, resource, action, team_id):
            raise PermissionDeniedError(f"Permission denied: {resource}:{action}")
    
    # ========== Role Management ==========
    
    async def assign_role(
        self,
        user_id: "UUID",
        org_id: "UUID",
        role: str,
    ) -> OrgMember:
        """Assign a role to a user in an organization.
        
        Args:
            user_id: The user's UUID.
            org_id: The organization's UUID.
            role: The role name (e.g., "org_admin", "org_member").
            
        Returns:
            The OrgMember record.
            
        Raises:
            OrganizationNotFoundError: If the organization doesn't exist.
            UserNotInOrganizationError: If the user is not an org member.
        """
        # Verify organization exists
        org = await self.session.get(Organization, org_id)
        if not org:
            raise OrganizationNotFoundError(str(org_id))
        
        # Get or create org membership
        result = await self.session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user_id,
                OrgMember.org_id == org_id,
            )
        )
        org_member = result.scalar_one_or_none()
        
        if org_member:
            org_member.role = role
        else:
            org_member = OrgMember(
                user_id=user_id,
                org_id=org_id,
                role=role,
            )
            self.session.add(org_member)
        
        await self.session.flush()
        return org_member
    
    async def assign_team_role(
        self,
        user_id: "UUID",
        team_id: "UUID",
        role: str,
    ) -> TeamMember:
        """Assign a role to a user in a team.
        
        Args:
            user_id: The user's UUID.
            team_id: The team's UUID.
            role: The role name (e.g., "team_admin", "team_member").
            
        Returns:
            The TeamMember record.
            
        Raises:
            TeamNotFoundError: If the team doesn't exist.
        """
        # Verify team exists
        team = await self.session.get(Team, team_id)
        if not team:
            raise TeamNotFoundError(str(team_id))
        
        # Get or create team membership
        result = await self.session.execute(
            select(TeamMember).where(
                TeamMember.user_id == user_id,
                TeamMember.team_id == team_id,
            )
        )
        team_member = result.scalar_one_or_none()
        
        if team_member:
            team_member.role = role
        else:
            team_member = TeamMember(
                user_id=user_id,
                team_id=team_id,
                role=role,
            )
            self.session.add(team_member)
        
        await self.session.flush()
        return team_member
    
    async def remove_from_organization(
        self,
        user_id: "UUID",
        org_id: "UUID",
    ) -> None:
        """Remove a user from an organization.
        
        Args:
            user_id: The user's UUID.
            org_id: The organization's UUID.
        """
        result = await self.session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user_id,
                OrgMember.org_id == org_id,
            )
        )
        org_member = result.scalar_one_or_none()
        
        if org_member:
            await self.session.delete(org_member)
    
    async def remove_from_team(
        self,
        user_id: "UUID",
        team_id: "UUID",
    ) -> None:
        """Remove a user from a team.
        
        Args:
            user_id: The user's UUID.
            team_id: The team's UUID.
        """
        result = await self.session.execute(
            select(TeamMember).where(
                TeamMember.user_id == user_id,
                TeamMember.team_id == team_id,
            )
        )
        team_member = result.scalar_one_or_none()
        
        if team_member:
            await self.session.delete(team_member)
    
    # ========== Custom Role Management ==========
    
    async def create_custom_role(
        self,
        org_id: "UUID",
        name: str,
        description: str | None,
        permission_strings: list[str],
    ) -> Role:
        """Create a custom role within an organization.
        
        Args:
            org_id: The organization's UUID.
            name: The role name.
            description: Optional role description.
            permission_strings: List of permission strings (e.g., ["api_key:create"]).
            
        Returns:
            The created Role.
            
        Raises:
            DuplicateRoleError: If a role with this name already exists in the org.
        """
        # Check for duplicate
        result = await self.session.execute(
            select(Role).where(
                Role.name == name,
                Role.org_id == org_id,
            )
        )
        if result.scalar_one_or_none():
            raise DuplicateRoleError(name)
        
        # Create role
        role = Role(
            name=name,
            description=description,
            org_id=org_id,
            is_system=False,
        )
        self.session.add(role)
        await self.session.flush()
        
        # Add permissions
        for perm_str in permission_strings:
            resource, action = perm_str.split(":")
            
            # Get or create permission
            result = await self.session.execute(
                select(PermissionModel).where(
                    PermissionModel.resource == resource,
                    PermissionModel.action == action,
                )
            )
            permission = result.scalar_one_or_none()
            
            if not permission:
                permission = PermissionModel(
                    resource=resource,
                    action=action,
                )
                self.session.add(permission)
                await self.session.flush()
            
            # Link permission to role
            role_permission = RolePermission(
                role_id=role.id,
                permission_id=permission.id,
            )
            self.session.add(role_permission)
        
        return role
    
    async def delete_custom_role(
        self,
        role_id: "UUID",
    ) -> None:
        """Delete a custom role.
        
        Args:
            role_id: The role's UUID.
            
        Raises:
            RoleNotFoundError: If the role doesn't exist.
            SystemRoleModificationError: If trying to delete a system role.
        """
        role = await self.session.get(Role, role_id)
        
        if not role:
            raise RoleNotFoundError(str(role_id))
        
        if role.is_system:
            raise SystemRoleModificationError(role.name)
        
        await self.session.delete(role)
    
    # ========== Membership Queries ==========
    
    async def get_user_organizations(
        self,
        user_id: "UUID",
    ) -> list[Organization]:
        """Get all organizations a user belongs to.
        
        Args:
            user_id: The user's UUID.
            
        Returns:
            List of organizations.
        """
        result = await self.session.execute(
            select(Organization)
            .join(OrgMember)
            .where(OrgMember.user_id == user_id)
        )
        return list(result.scalars().all())
    
    async def get_user_teams(
        self,
        user_id: "UUID",
        org_id: "UUID | None" = None,
    ) -> list[Team]:
        """Get all teams a user belongs to.
        
        Args:
            user_id: The user's UUID.
            org_id: Optional organization ID to filter by.
            
        Returns:
            List of teams.
        """
        query = select(Team).join(TeamMember).where(TeamMember.user_id == user_id)
        
        if org_id:
            query = query.where(Team.org_id == org_id)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_organization_members(
        self,
        org_id: "UUID",
    ) -> list[tuple[User, str]]:
        """Get all members of an organization with their roles.
        
        Args:
            org_id: The organization's UUID.
            
        Returns:
            List of (User, role) tuples.
        """
        result = await self.session.execute(
            select(User, OrgMember.role)
            .join(OrgMember)
            .where(OrgMember.org_id == org_id)
        )
        return list(result.all())
    
    async def get_team_members(
        self,
        team_id: "UUID",
    ) -> list[tuple[User, str]]:
        """Get all members of a team with their roles.
        
        Args:
            team_id: The team's UUID.
            
        Returns:
            List of (User, role) tuples.
        """
        result = await self.session.execute(
            select(User, TeamMember.role)
            .join(TeamMember)
            .where(TeamMember.team_id == team_id)
        )
        return list(result.all())
