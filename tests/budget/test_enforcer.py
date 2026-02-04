"""Tests for budget enforcer."""

import pytest
from decimal import Decimal
from uuid import uuid4, UUID

from deltallm.budget.enforcer import BudgetEnforcer, BudgetExceededError, BudgetCheckResult


class TestBudgetEnforcer:
    """Test cases for BudgetEnforcer."""
    
    @pytest.fixture
    async def enforcer(self, db_session):
        """Create a budget enforcer."""
        return BudgetEnforcer(db_session)
    
    @pytest.fixture
    async def org_with_budget(self, db_session):
        """Create an organization with budget."""
        from deltallm.db.models import Organization
        
        org = Organization(
            id=uuid4(),
            name="Test Org",
            slug="test-org",
            max_budget=Decimal("1000.00"),
            spend=Decimal("500.00"),
        )
        db_session.add(org)
        await db_session.commit()
        return org
    
    @pytest.fixture
    async def team_with_budget(self, db_session, org_with_budget):
        """Create a team with budget."""
        from deltallm.db.models import Team
        
        team = Team(
            id=uuid4(),
            name="Test Team",
            slug="test-team",
            org_id=org_with_budget.id,
            max_budget=Decimal("500.00"),
            spend=Decimal("200.00"),
        )
        db_session.add(team)
        await db_session.commit()
        return team
    
    @pytest.fixture
    async def api_key_with_budget(self, db_session, org_with_budget, team_with_budget):
        """Create an API key with budget."""
        from deltallm.db.models import APIKey
        
        key = APIKey(
            id=uuid4(),
            key_hash="test-hash",
            org_id=org_with_budget.id,
            team_id=team_with_budget.id,
            max_budget=Decimal("100.00"),
            spend=Decimal("50.00"),
        )
        db_session.add(key)
        await db_session.commit()
        return key
    
    @pytest.mark.asyncio
    async def test_check_org_budget_within_limit(self, enforcer, org_with_budget):
        """Test checking org budget that is within limit."""
        result = await enforcer.check_org_budget(org_with_budget.id)
        
        assert result.allowed is True
        assert result.level == "organization"
        assert result.limit == Decimal("1000.00")
        assert result.current == Decimal("500.00")
    
    @pytest.mark.asyncio
    async def test_check_org_budget_exceeded(self, db_session, enforcer, org_with_budget):
        """Test checking org budget that is exceeded."""
        # Update spend to exceed budget
        org_with_budget.spend = Decimal("1100.00")
        await db_session.commit()
        
        result = await enforcer.check_org_budget(org_with_budget.id)
        
        assert result.allowed is False
        assert result.level == "organization"
        assert result.is_exceeded is True
    
    @pytest.mark.asyncio
    async def test_check_org_budget_no_limit(self, db_session, enforcer):
        """Test checking org without budget limit."""
        from deltallm.db.models import Organization
        
        org = Organization(
            id=uuid4(),
            name="Unlimited Org",
            slug="unlimited-org",
            max_budget=None,
            spend=Decimal("999999.00"),
        )
        db_session.add(org)
        await db_session.commit()
        
        result = await enforcer.check_org_budget(org.id)
        
        assert result.allowed is True
        assert result.limit is None
    
    @pytest.mark.asyncio
    async def test_check_team_budget_within_limit(self, enforcer, team_with_budget):
        """Test checking team budget within limit."""
        result = await enforcer.check_team_budget(team_with_budget.id)
        
        assert result.allowed is True
        assert result.level == "team"
        assert result.limit == Decimal("500.00")
    
    @pytest.mark.asyncio
    async def test_check_key_budget_within_limit(self, enforcer, api_key_with_budget):
        """Test checking API key budget within limit."""
        result = await enforcer.check_key_budget(api_key_with_budget.id)
        
        assert result.allowed is True
        assert result.level == "api_key"
        assert result.limit == Decimal("100.00")
    
    @pytest.mark.asyncio
    async def test_check_all_budgets_hierarchical(self, enforcer, api_key_with_budget):
        """Test checking all budget levels hierarchically."""
        result = await enforcer.check_all_budgets(api_key_with_budget.id)
        
        assert result.allowed is True
    
    @pytest.mark.asyncio
    async def test_check_all_budgets_key_exceeded(
        self, db_session, enforcer, api_key_with_budget
    ):
        """Test that key budget violation is detected first."""
        api_key_with_budget.spend = Decimal("150.00")
        await db_session.commit()
        
        result = await enforcer.check_all_budgets(api_key_with_budget.id)
        
        assert result.allowed is False
        assert result.level == "api_key"
    
    @pytest.mark.asyncio
    async def test_check_all_budgets_team_exceeded(
        self, db_session, enforcer, team_with_budget, api_key_with_budget
    ):
        """Test that team budget violation is detected when key is OK."""
        # Make team exceed budget but key is OK
        team_with_budget.spend = Decimal("550.00")
        await db_session.commit()
        
        result = await enforcer.check_all_budgets(api_key_with_budget.id)
        
        assert result.allowed is False
        assert result.level == "team"
    
    @pytest.mark.asyncio
    async def test_enforce_budgets_raises_on_exceeded(self, db_session, enforcer, org_with_budget):
        """Test that enforce_budgets raises exception when exceeded."""
        from deltallm.db.models import APIKey
        
        org_with_budget.spend = Decimal("1100.00")
        await db_session.commit()
        
        key = APIKey(
            id=uuid4(),
            key_hash="test-hash-2",
            org_id=org_with_budget.id,
            max_budget=None,
            spend=Decimal("0.00"),
        )
        db_session.add(key)
        await db_session.commit()
        
        with pytest.raises(BudgetExceededError) as exc_info:
            await enforcer.enforce_budgets(key.id)
        
        assert "organization" in str(exc_info.value).lower()
        assert exc_info.value.level == "organization"
    
    @pytest.mark.asyncio
    async def test_enforce_budgets_passes_when_within_limit(
        self, enforcer, api_key_with_budget
    ):
        """Test that enforce_budgets passes when within budget."""
        # Should not raise
        await enforcer.enforce_budgets(api_key_with_budget.id)
    
    @pytest.mark.asyncio
    async def test_check_budget_with_estimated_cost(
        self, enforcer, api_key_with_budget
    ):
        """Test budget check with estimated cost for request."""
        # Key has $50 spent of $100 limit, request costs $30
        result = await enforcer.check_key_budget(
            api_key_with_budget.id,
            estimated_cost=Decimal("30.00")
        )
        
        assert result.allowed is True
        
        # Request costs $60, would exceed
        result = await enforcer.check_key_budget(
            api_key_with_budget.id,
            estimated_cost=Decimal("60.00")
        )
        
        assert result.allowed is False
    
    @pytest.mark.asyncio
    async def test_check_nonexistent_org(self, enforcer):
        """Test checking budget for non-existent organization."""
        result = await enforcer.check_org_budget(uuid4())
        
        assert result.allowed is False
        assert "not found" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_check_nonexistent_key(self, enforcer):
        """Test checking budget for non-existent API key."""
        result = await enforcer.check_key_budget(uuid4())
        
        assert result.allowed is False
        assert "not found" in result.message.lower()
