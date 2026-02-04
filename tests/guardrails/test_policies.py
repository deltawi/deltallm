"""Tests for guardrails policies."""

import pytest

from deltallm.guardrails.policies import (
    ContentPolicy,
    PolicyEngine,
    PolicyViolation,
    PolicySeverity,
    PolicyAction,
    DEFAULT_POLICY,
    STRICT_POLICY,
    PERMISSIVE_POLICY,
)


class TestContentPolicy:
    """Test cases for ContentPolicy."""
    
    def test_default_policy_creation(self):
        """Test creating a default policy."""
        policy = ContentPolicy(
            id="test-policy",
            name="Test Policy",
            description="A test policy",
        )
        
        assert policy.id == "test-policy"
        assert policy.name == "Test Policy"
        assert policy.enable_pii_filter is True
        assert policy.violation_action == PolicyAction.BLOCK
    
    def test_policy_to_dict(self):
        """Test converting policy to dictionary."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            description="Test policy",
            blocked_topics={"gambling", "drugs"},
            enable_pii_filter=True,
            pii_action="redact",
        )
        
        data = policy.to_dict()
        
        assert data["id"] == "test"
        assert data["name"] == "Test"
        assert "gambling" in data["blocked_topics"]
        assert data["enable_pii_filter"] is True
        assert data["pii_action"] == "redact"
    
    def test_policy_from_dict(self):
        """Test creating policy from dictionary."""
        data = {
            "id": "test",
            "name": "Test Policy",
            "description": "Test description",
            "blocked_topics": ["topic1", "topic2"],
            "allowed_topics": ["topic3"],
            "enable_pii_filter": False,
            "enable_toxicity_filter": True,
            "pii_action": "block",
            "toxicity_action": "flag",
            "violation_action": "warn",
            "alert_on_violation": False,
        }
        
        policy = ContentPolicy.from_dict(data)
        
        assert policy.id == "test"
        assert "topic1" in policy.blocked_topics
        assert "topic3" in policy.allowed_topics
        assert policy.enable_pii_filter is False
        assert policy.violation_action == PolicyAction.WARN
    
    def test_predefined_default_policy(self):
        """Test the predefined default policy."""
        policy = DEFAULT_POLICY
        
        assert policy.id == "default"
        assert policy.enable_pii_filter is True
        assert policy.enable_toxicity_filter is True
        assert policy.enable_injection_filter is True
        assert policy.pii_action == "redact"
        assert policy.toxicity_action == "block"
    
    def test_predefined_strict_policy(self):
        """Test the predefined strict policy."""
        policy = STRICT_POLICY
        
        assert policy.id == "strict"
        assert policy.pii_action == "block"
        assert policy.toxicity_action == "block"
        assert policy.injection_action == "block"
        assert policy.violation_action == PolicyAction.BLOCK
    
    def test_predefined_permissive_policy(self):
        """Test the predefined permissive policy."""
        policy = PERMISSIVE_POLICY
        
        assert policy.id == "permissive"
        assert policy.enable_pii_filter is False
        assert policy.violation_action == PolicyAction.WARN


class TestPolicyEngine:
    """Test cases for PolicyEngine."""
    
    def test_check_content_with_blocked_topic(self):
        """Test detecting blocked topic in content."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            blocked_topics={"gambling", "casino"},
        )
        engine = PolicyEngine(policy)
        
        violations = engine.check_content("Let's go to the casino tonight!")
        
        assert len(violations) == 1
        assert violations[0].severity == PolicySeverity.HIGH
        assert "casino" in violations[0].message
    
    def test_check_content_with_multiple_blocked_topics(self):
        """Test detecting multiple blocked topics."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            blocked_topics={"gambling", "drugs"},
        )
        engine = PolicyEngine(policy)
        
        violations = engine.check_content("Gambling and drugs are bad.")
        
        assert len(violations) == 2
    
    def test_check_content_no_violations(self):
        """Test clean content has no violations."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            blocked_topics={"gambling"},
        )
        engine = PolicyEngine(policy)
        
        violations = engine.check_content("This is completely safe content.")
        
        assert len(violations) == 0
    
    def test_check_content_custom_blocked_patterns(self):
        """Test custom regex pattern blocking."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            custom_blocked_patterns=[r"SECRET-\d+"],
        )
        engine = PolicyEngine(policy)
        
        violations = engine.check_content("The code is SECRET-12345.")
        
        assert len(violations) == 1
    
    def test_get_action_allow_when_no_violations(self):
        """Test that no violations results in ALLOW action."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            violation_action=PolicyAction.BLOCK,
        )
        engine = PolicyEngine(policy)
        
        action = engine.get_action([])
        
        assert action == PolicyAction.ALLOW
    
    def test_get_action_block_on_high_severity(self):
        """Test that high severity violations result in BLOCK."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            violation_action=PolicyAction.BLOCK,
        )
        engine = PolicyEngine(policy)
        
        violations = [
            PolicyViolation(
                policy_id="test",
                policy_name="Test",
                severity=PolicySeverity.HIGH,
                message="High severity issue",
            )
        ]
        
        action = engine.get_action(violations)
        
        assert action == PolicyAction.BLOCK
    
    def test_get_action_warn_on_low_severity(self):
        """Test that low severity violations may result in WARN."""
        policy = ContentPolicy(
            id="test",
            name="Test",
            violation_action=PolicyAction.WARN,
        )
        engine = PolicyEngine(policy)
        
        violations = [
            PolicyViolation(
                policy_id="test",
                policy_name="Test",
                severity=PolicySeverity.LOW,
                message="Low severity issue",
            )
        ]
        
        action = engine.get_action(violations)
        
        assert action == PolicyAction.ALLOW  # Low severity with WARN policy


class TestPolicyViolation:
    """Test cases for PolicyViolation."""
    
    def test_violation_creation(self):
        """Test creating a policy violation."""
        violation = PolicyViolation(
            policy_id="policy-1",
            policy_name="Test Policy",
            severity=PolicySeverity.HIGH,
            message="Content violated policy",
            details={"topic": "gambling"},
        )
        
        assert violation.policy_id == "policy-1"
        assert violation.severity == PolicySeverity.HIGH
        assert violation.details["topic"] == "gambling"
