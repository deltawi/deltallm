"""Tests for team-provider access control."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import (
    Organization,
    Team,
    User,
    OrgMember,
    TeamMember,
    ProviderConfig,
    ModelDeployment,
    TeamProviderAccess,
)
from deltallm.proxy.dependencies import (
    check_org_admin,
    check_org_member,
    get_user_org_ids,
)


@pytest_asyncio.fixture
async def test_org(db_session: AsyncSession) -> Organization:
    """Create a test organization."""
    org = Organization(
        id=uuid4(),
        name="Test Organization",
        slug="test-org",
        spend=Decimal("0"),
        settings={},
    )
    db_session.add(org)
    await db_session.commit()
    return org


@pytest_asyncio.fixture
async def test_team(db_session: AsyncSession, test_org: Organization) -> Team:
    """Create a test team."""
    team = Team(
        id=uuid4(),
        name="Test Team",
        slug="test-team",
        org_id=test_org.id,
        spend=Decimal("0"),
        settings={},
    )
    db_session.add(team)
    await db_session.commit()
    return team


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        id=uuid4(),
        email="test@example.com",
        is_superuser=False,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def test_superuser(db_session: AsyncSession) -> User:
    """Create a test superuser."""
    user = User(
        id=uuid4(),
        email="superuser@example.com",
        is_superuser=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def test_provider(db_session: AsyncSession, test_org: Organization) -> ProviderConfig:
    """Create a test provider config."""
    provider = ProviderConfig(
        id=uuid4(),
        name="Test Provider",
        provider_type="openai",
        org_id=test_org.id,
        is_active=True,
        settings={},
    )
    db_session.add(provider)
    await db_session.commit()
    return provider


class TestOrgAdminCheck:
    """Test org admin permission checks."""

    async def test_superuser_is_admin(
        self,
        db_session: AsyncSession,
        test_superuser: User,
        test_org: Organization,
    ):
        """Test that superuser is always considered org admin."""
        result = await check_org_admin(test_superuser, test_org.id, db_session)
        assert result is True

    async def test_non_member_is_not_admin(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test that non-member is not org admin."""
        result = await check_org_admin(test_user, test_org.id, db_session)
        assert result is False

    async def test_member_role_is_not_admin(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test that member role is not org admin."""
        # Add user as member
        membership = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=test_org.id,
            role="member",
        )
        db_session.add(membership)
        await db_session.commit()

        result = await check_org_admin(test_user, test_org.id, db_session)
        assert result is False

    async def test_admin_role_is_admin(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test that admin role is org admin."""
        # Add user as admin
        membership = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=test_org.id,
            role="org_admin",
        )
        db_session.add(membership)
        await db_session.commit()

        result = await check_org_admin(test_user, test_org.id, db_session)
        assert result is True

    async def test_owner_role_is_admin(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test that owner role is org admin."""
        # Add user as owner
        membership = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=test_org.id,
            role="org_owner",
        )
        db_session.add(membership)
        await db_session.commit()

        result = await check_org_admin(test_user, test_org.id, db_session)
        assert result is True


class TestOrgMemberCheck:
    """Test org member checks."""

    async def test_superuser_is_member(
        self,
        db_session: AsyncSession,
        test_superuser: User,
        test_org: Organization,
    ):
        """Test that superuser is always considered org member."""
        result = await check_org_member(test_superuser, test_org.id, db_session)
        assert result is True

    async def test_non_member_is_not_member(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test that non-member is not org member."""
        result = await check_org_member(test_user, test_org.id, db_session)
        assert result is False

    async def test_member_role_is_member(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test that any org role makes user a member."""
        # Add user as member
        membership = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=test_org.id,
            role="member",
        )
        db_session.add(membership)
        await db_session.commit()

        result = await check_org_member(test_user, test_org.id, db_session)
        assert result is True


class TestTeamProviderAccess:
    """Test team-provider access model."""

    async def test_create_team_provider_access(
        self,
        db_session: AsyncSession,
        test_team: Team,
        test_provider: ProviderConfig,
        test_user: User,
    ):
        """Test creating team-provider access."""
        access = TeamProviderAccess(
            id=uuid4(),
            team_id=test_team.id,
            provider_config_id=test_provider.id,
            granted_by=test_user.id,
        )
        db_session.add(access)
        await db_session.commit()

        # Verify access was created
        result = await db_session.execute(
            select(TeamProviderAccess).where(
                TeamProviderAccess.team_id == test_team.id,
                TeamProviderAccess.provider_config_id == test_provider.id,
            )
        )
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.team_id == test_team.id
        assert fetched.provider_config_id == test_provider.id
        assert fetched.granted_by == test_user.id

    async def test_team_provider_access_unique_constraint(
        self,
        db_session: AsyncSession,
        test_team: Team,
        test_provider: ProviderConfig,
        test_user: User,
    ):
        """Test that duplicate team-provider access is prevented."""
        from sqlalchemy.exc import IntegrityError

        # Create first access
        access1 = TeamProviderAccess(
            id=uuid4(),
            team_id=test_team.id,
            provider_config_id=test_provider.id,
            granted_by=test_user.id,
        )
        db_session.add(access1)
        await db_session.commit()

        # Try to create duplicate
        access2 = TeamProviderAccess(
            id=uuid4(),
            team_id=test_team.id,
            provider_config_id=test_provider.id,
            granted_by=test_user.id,
        )
        db_session.add(access2)

        with pytest.raises(IntegrityError):
            await db_session.commit()


class TestGetUserOrgIds:
    """Test get_user_org_ids function."""

    async def test_user_with_no_orgs(
        self,
        db_session: AsyncSession,
        test_user: User,
    ):
        """Test user with no organizations."""
        org_ids = await get_user_org_ids(test_user, db_session)
        assert org_ids == []

    async def test_user_with_single_org(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test user with single organization."""
        # Add user to org
        membership = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=test_org.id,
            role="member",
        )
        db_session.add(membership)
        await db_session.commit()

        org_ids = await get_user_org_ids(test_user, db_session)
        assert len(org_ids) == 1
        assert test_org.id in org_ids

    async def test_user_with_multiple_orgs(
        self,
        db_session: AsyncSession,
        test_user: User,
        test_org: Organization,
    ):
        """Test user with multiple organizations."""
        # Create second org
        org2 = Organization(
            id=uuid4(),
            name="Second Organization",
            slug="second-org",
            spend=Decimal("0"),
            settings={},
        )
        db_session.add(org2)
        await db_session.commit()

        # Add user to both orgs
        membership1 = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=test_org.id,
            role="member",
        )
        membership2 = OrgMember(
            id=uuid4(),
            user_id=test_user.id,
            org_id=org2.id,
            role="admin",
        )
        db_session.add(membership1)
        db_session.add(membership2)
        await db_session.commit()

        org_ids = await get_user_org_ids(test_user, db_session)
        assert len(org_ids) == 2
        assert test_org.id in org_ids
        assert org2.id in org_ids
