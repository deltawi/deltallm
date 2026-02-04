"""Audit logging for RBAC operations.

This module provides the AuditLogger class for tracking changes
to organizations, teams, members, and other RBAC-related entities.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import AuditLog

if TYPE_CHECKING:
    from deltallm.db.models import User


class AuditLogger:
    """Logger for audit events.
    
    Tracks all changes to RBAC entities for compliance and security.
    
    Example:
        async with get_session() as session:
            logger = AuditLogger(session)
            await logger.log(
                action="org_update",
                user_id=current_user.id,
                org_id=org.id,
                resource_type="organization",
                resource_id=org.id,
                old_values={"name": old_name},
                new_values={"name": new_name},
            )
    """
    
    def __init__(self, session: AsyncSession):
        """Initialize the audit logger.
        
        Args:
            session: SQLAlchemy async session
        """
        self.session = session
    
    async def log(
        self,
        action: str,
        user_id: Optional[UUID] = None,
        org_id: Optional[UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[UUID] = None,
        old_values: Optional[dict[str, Any]] = None,
        new_values: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        """Create an audit log entry.
        
        Args:
            action: The action performed (e.g., "org_create", "member_add")
            user_id: The user who performed the action
            org_id: The organization context
            resource_type: Type of resource affected (e.g., "organization", "team")
            resource_id: ID of the resource affected
            old_values: Previous values (for updates)
            new_values: New values (for creates/updates)
            ip_address: Client IP address
            user_agent: Client user agent
            
        Returns:
            The created AuditLog entry
        """
        log_entry = AuditLog(
            user_id=user_id,
            org_id=org_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            old_values=old_values or {},
            new_values=new_values or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        
        self.session.add(log_entry)
        await self.session.flush()
        
        return log_entry
    
    async def log_organization_create(
        self,
        user_id: UUID,
        org_id: UUID,
        org_data: dict[str, Any],
        **kwargs,
    ) -> AuditLog:
        """Log organization creation."""
        return await self.log(
            action="org_create",
            user_id=user_id,
            org_id=org_id,
            resource_type="organization",
            resource_id=org_id,
            new_values=org_data,
            **kwargs,
        )
    
    async def log_organization_update(
        self,
        user_id: UUID,
        org_id: UUID,
        old_values: dict[str, Any],
        new_values: dict[str, Any],
        **kwargs,
    ) -> AuditLog:
        """Log organization update."""
        return await self.log(
            action="org_update",
            user_id=user_id,
            org_id=org_id,
            resource_type="organization",
            resource_id=org_id,
            old_values=old_values,
            new_values=new_values,
            **kwargs,
        )
    
    async def log_organization_delete(
        self,
        user_id: UUID,
        org_id: UUID,
        org_data: dict[str, Any],
        **kwargs,
    ) -> AuditLog:
        """Log organization deletion."""
        return await self.log(
            action="org_delete",
            user_id=user_id,
            org_id=org_id,
            resource_type="organization",
            resource_id=org_id,
            old_values=org_data,
            **kwargs,
        )
    
    async def log_team_create(
        self,
        user_id: UUID,
        org_id: UUID,
        team_id: UUID,
        team_data: dict[str, Any],
        **kwargs,
    ) -> AuditLog:
        """Log team creation."""
        return await self.log(
            action="team_create",
            user_id=user_id,
            org_id=org_id,
            resource_type="team",
            resource_id=team_id,
            new_values=team_data,
            **kwargs,
        )
    
    async def log_team_update(
        self,
        user_id: UUID,
        org_id: UUID,
        team_id: UUID,
        old_values: dict[str, Any],
        new_values: dict[str, Any],
        **kwargs,
    ) -> AuditLog:
        """Log team update."""
        return await self.log(
            action="team_update",
            user_id=user_id,
            org_id=org_id,
            resource_type="team",
            resource_id=team_id,
            old_values=old_values,
            new_values=new_values,
            **kwargs,
        )
    
    async def log_team_delete(
        self,
        user_id: UUID,
        org_id: UUID,
        team_id: UUID,
        team_data: dict[str, Any],
        **kwargs,
    ) -> AuditLog:
        """Log team deletion."""
        return await self.log(
            action="team_delete",
            user_id=user_id,
            org_id=org_id,
            resource_type="team",
            resource_id=team_id,
            old_values=team_data,
            **kwargs,
        )
    
    async def log_org_member_add(
        self,
        user_id: UUID,
        org_id: UUID,
        member_id: UUID,
        role: str,
        **kwargs,
    ) -> AuditLog:
        """Log organization member addition."""
        return await self.log(
            action="org_member_add",
            user_id=user_id,
            org_id=org_id,
            resource_type="org_member",
            resource_id=member_id,
            new_values={"user_id": str(member_id), "role": role},
            **kwargs,
        )
    
    async def log_org_member_update(
        self,
        user_id: UUID,
        org_id: UUID,
        member_id: UUID,
        old_role: str,
        new_role: str,
        **kwargs,
    ) -> AuditLog:
        """Log organization member role update."""
        return await self.log(
            action="org_member_update",
            user_id=user_id,
            org_id=org_id,
            resource_type="org_member",
            resource_id=member_id,
            old_values={"role": old_role},
            new_values={"role": new_role},
            **kwargs,
        )
    
    async def log_org_member_remove(
        self,
        user_id: UUID,
        org_id: UUID,
        member_id: UUID,
        role: str,
        **kwargs,
    ) -> AuditLog:
        """Log organization member removal."""
        return await self.log(
            action="org_member_remove",
            user_id=user_id,
            org_id=org_id,
            resource_type="org_member",
            resource_id=member_id,
            old_values={"user_id": str(member_id), "role": role},
            **kwargs,
        )
    
    async def log_team_member_add(
        self,
        user_id: UUID,
        org_id: UUID,
        team_id: UUID,
        member_id: UUID,
        role: str,
        **kwargs,
    ) -> AuditLog:
        """Log team member addition."""
        return await self.log(
            action="team_member_add",
            user_id=user_id,
            org_id=org_id,
            resource_type="team_member",
            resource_id=member_id,
            new_values={"team_id": str(team_id), "user_id": str(member_id), "role": role},
            **kwargs,
        )
    
    async def log_team_member_update(
        self,
        user_id: UUID,
        org_id: UUID,
        team_id: UUID,
        member_id: UUID,
        old_role: str,
        new_role: str,
        **kwargs,
    ) -> AuditLog:
        """Log team member role update."""
        return await self.log(
            action="team_member_update",
            user_id=user_id,
            org_id=org_id,
            resource_type="team_member",
            resource_id=member_id,
            old_values={"team_id": str(team_id), "role": old_role},
            new_values={"team_id": str(team_id), "role": new_role},
            **kwargs,
        )
    
    async def log_team_member_remove(
        self,
        user_id: UUID,
        org_id: UUID,
        team_id: UUID,
        member_id: UUID,
        role: str,
        **kwargs,
    ) -> AuditLog:
        """Log team member removal."""
        return await self.log(
            action="team_member_remove",
            user_id=user_id,
            org_id=org_id,
            resource_type="team_member",
            resource_id=member_id,
            old_values={"team_id": str(team_id), "user_id": str(member_id), "role": role},
            **kwargs,
        )
    
    async def log_role_assignment(
        self,
        user_id: UUID,
        org_id: UUID,
        target_user_id: UUID,
        role: str,
        **kwargs,
    ) -> AuditLog:
        """Log role assignment."""
        return await self.log(
            action="role_assign",
            user_id=user_id,
            org_id=org_id,
            resource_type="user",
            resource_id=target_user_id,
            new_values={"role": role},
            **kwargs,
        )
    
    async def log_permission_denied(
        self,
        user_id: UUID,
        org_id: Optional[UUID],
        action: str,
        permission: str,
        **kwargs,
    ) -> AuditLog:
        """Log permission denied event."""
        return await self.log(
            action="permission_denied",
            user_id=user_id,
            org_id=org_id,
            resource_type="permission",
            old_values={"attempted_action": action, "required_permission": permission},
            **kwargs,
        )
