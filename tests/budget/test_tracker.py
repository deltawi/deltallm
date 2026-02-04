"""Tests for budget tracker."""

import pytest
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timedelta

from deltallm.budget.tracker import BudgetTracker


class TestBudgetTracker:
    """Test cases for BudgetTracker."""
    
    @pytest.fixture
    async def tracker(self, db_session):
        """Create a budget tracker."""
        return BudgetTracker(db_session)
    
    @pytest.fixture
    async def org(self, db_session):
        """Create test organization."""
        from deltallm.db.models import Organization
        
        org = Organization(
            id=uuid4(),
            name="Test Org",
            slug="test-org",
            max_budget=Decimal("1000.00"),
            spend=Decimal("0.00"),
        )
        db_session.add(org)
        await db_session.commit()
        return org
    
    @pytest.fixture
    async def team(self, db_session, org):
        """Create test team."""
        from deltallm.db.models import Team
        
        team = Team(
            id=uuid4(),
            name="Test Team",
            slug="test-team",
            org_id=org.id,
            max_budget=Decimal("500.00"),
            spend=Decimal("0.00"),
        )
        db_session.add(team)
        await db_session.commit()
        return team
    
    @pytest.fixture
    async def api_key(self, db_session, org, team):
        """Create test API key."""
        from deltallm.db.models import APIKey
        
        key = APIKey(
            id=uuid4(),
            key_hash="test-hash",
            org_id=org.id,
            team_id=team.id,
            max_budget=Decimal("100.00"),
            spend=Decimal("0.00"),
        )
        db_session.add(key)
        await db_session.commit()
        return key
    
    @pytest.mark.asyncio
    async def test_record_spend(self, tracker, org, team, api_key, db_session):
        """Test recording a spend log entry."""
        spend_log = await tracker.record_spend(
            request_id="req-001",
            api_key_id=api_key.id,
            user_id=None,
            team_id=team.id,
            org_id=org.id,
            model="gpt-4",
            provider="openai",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost=Decimal("0.015"),
            latency_ms=500.0,
            status="success",
        )
        
        assert spend_log.id is not None
        assert spend_log.request_id == "req-001"
        assert spend_log.spend == Decimal("0.015")
        
        # Verify budgets were updated
        await db_session.refresh(org)
        await db_session.refresh(team)
        await db_session.refresh(api_key)
        
        assert org.spend == Decimal("0.015")
        assert team.spend == Decimal("0.015")
        assert api_key.spend == Decimal("0.015")
    
    @pytest.mark.asyncio
    async def test_record_spend_without_org(self, tracker, api_key, db_session):
        """Test recording spend without org context."""
        spend_log = await tracker.record_spend(
            request_id="req-002",
            api_key_id=api_key.id,
            user_id=None,
            team_id=None,
            org_id=None,
            model="gpt-4",
            provider="openai",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost=Decimal("0.015"),
            latency_ms=500.0,
            status="success",
        )
        
        assert spend_log.id is not None
        # Only key spend should be updated
        await db_session.refresh(api_key)
        assert api_key.spend == Decimal("0.015")
    
    @pytest.mark.asyncio
    async def test_get_org_spend(self, tracker, org):
        """Test getting org spend."""
        org.spend = Decimal("500.00")
        
        spend = await tracker.get_org_spend(org.id)
        
        assert spend == Decimal("500.00")
    
    @pytest.mark.asyncio
    async def test_get_team_spend(self, tracker, team):
        """Test getting team spend."""
        team.spend = Decimal("250.00")
        
        spend = await tracker.get_team_spend(team.id)
        
        assert spend == Decimal("250.00")
    
    @pytest.mark.asyncio
    async def test_get_key_spend(self, tracker, api_key):
        """Test getting API key spend."""
        api_key.spend = Decimal("50.00")
        
        spend = await tracker.get_key_spend(api_key.id)
        
        assert spend == Decimal("50.00")
    
    @pytest.mark.asyncio
    async def test_get_spend_logs_with_filters(self, tracker, org, db_session):
        """Test querying spend logs with filters."""
        from deltallm.db.models import SpendLog
        
        # Create some spend logs
        for i in range(5):
            log = SpendLog(
                id=uuid4(),
                request_id=f"req-{i}",
                org_id=org.id,
                model="gpt-4",
                spend=Decimal("0.01") * (i + 1),
                status="success",
                created_at=datetime.utcnow() - timedelta(hours=i),
            )
            db_session.add(log)
        await db_session.commit()
        
        # Query logs
        logs = await tracker.get_spend_logs(
            org_id=org.id,
            model="gpt-4",
            limit=10,
        )
        
        assert len(logs) == 5
    
    @pytest.mark.asyncio
    async def test_reset_key_spend(self, tracker, api_key, db_session):
        """Test resetting API key spend."""
        api_key.spend = Decimal("100.00")
        await db_session.commit()
        
        await tracker.reset_key_spend(api_key.id)
        await db_session.refresh(api_key)
        
        assert api_key.spend == Decimal("0.00")
    
    @pytest.mark.asyncio
    async def test_reset_team_spend(self, tracker, team, db_session):
        """Test resetting team spend."""
        team.spend = Decimal("500.00")
        await db_session.commit()
        
        await tracker.reset_team_spend(team.id)
        await db_session.refresh(team)
        
        assert team.spend == Decimal("0.00")
    
    @pytest.mark.asyncio
    async def test_reset_org_spend(self, tracker, org, db_session):
        """Test resetting org spend."""
        org.spend = Decimal("1000.00")
        await db_session.commit()
        
        await tracker.reset_org_spend(org.id)
        await db_session.refresh(org)
        
        assert org.spend == Decimal("0.00")
    
    @pytest.mark.asyncio
    async def test_record_spend_with_error(self, tracker, api_key):
        """Test recording failed request spend."""
        spend_log = await tracker.record_spend(
            request_id="req-error",
            api_key_id=api_key.id,
            user_id=None,
            team_id=None,
            org_id=None,
            model="gpt-4",
            provider="openai",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost=Decimal("0.00"),
            latency_ms=0.0,
            status="failure",
            error_message="Rate limit exceeded",
        )
        
        assert spend_log.status == "failure"
        assert spend_log.error_message == "Rate limit exceeded"
    
    @pytest.mark.asyncio
    async def test_record_spend_with_metadata(self, tracker, api_key):
        """Test recording spend with request metadata."""
        spend_log = await tracker.record_spend(
            request_id="req-meta",
            api_key_id=api_key.id,
            user_id=None,
            team_id=None,
            org_id=None,
            model="gpt-4",
            provider="openai",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost=Decimal("0.015"),
            latency_ms=500.0,
            status="success",
            request_tags=["production", "chatbot"],
            metadata={"client_version": "1.0.0", "feature": "support"},
        )
        
        assert spend_log.request_tags == ["production", "chatbot"]
        assert spend_log.request_metadata["client_version"] == "1.0.0"
