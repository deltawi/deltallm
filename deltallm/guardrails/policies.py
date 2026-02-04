"""Content policies for enforcing guardrails.

Policies define rules for what content is allowed and
how violations should be handled.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class PolicySeverity(str, Enum):
    """Severity levels for policy violations."""
    
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyAction(str, Enum):
    """Actions to take on policy violation."""
    
    ALLOW = "allow"  # Allow the content
    WARN = "warn"    # Allow with warning
    BLOCK = "block"  # Block the content
    QUARANTINE = "quarantine"  # Queue for review


@dataclass
class PolicyViolation:
    """A violation of a content policy."""
    
    policy_id: str
    policy_name: str
    severity: PolicySeverity
    message: str
    details: Dict = field(default_factory=dict)


@dataclass
class ContentPolicy:
    """A content policy defining allowed/blocked content.
    
    Policies can be scoped to organizations, teams, or API keys.
    """
    
    id: str
    name: str
    description: str
    
    # Content rules
    blocked_topics: Set[str] = field(default_factory=set)
    allowed_topics: Set[str] = field(default_factory=set)
    
    # Filters to apply
    enable_pii_filter: bool = True
    enable_toxicity_filter: bool = True
    enable_injection_filter: bool = True
    
    # Filter actions
    pii_action: str = "redact"  # block, redact, flag, log
    toxicity_action: str = "block"
    injection_action: str = "block"
    
    # Custom patterns (regex strings)
    custom_blocked_patterns: List[str] = field(default_factory=list)
    custom_allowed_patterns: List[str] = field(default_factory=list)
    
    # Rate limiting
    max_requests_per_minute: Optional[int] = None
    max_tokens_per_day: Optional[int] = None
    
    # Violation handling
    violation_action: PolicyAction = PolicyAction.BLOCK
    alert_on_violation: bool = True
    
    def to_dict(self) -> Dict:
        """Convert policy to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "blocked_topics": list(self.blocked_topics),
            "allowed_topics": list(self.allowed_topics),
            "enable_pii_filter": self.enable_pii_filter,
            "enable_toxicity_filter": self.enable_toxicity_filter,
            "enable_injection_filter": self.enable_injection_filter,
            "pii_action": self.pii_action,
            "toxicity_action": self.toxicity_action,
            "injection_action": self.injection_action,
            "custom_blocked_patterns": self.custom_blocked_patterns,
            "custom_allowed_patterns": self.custom_allowed_patterns,
            "max_requests_per_minute": self.max_requests_per_minute,
            "max_tokens_per_day": self.max_tokens_per_day,
            "violation_action": self.violation_action.value,
            "alert_on_violation": self.alert_on_violation,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ContentPolicy":
        """Create policy from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            blocked_topics=set(data.get("blocked_topics", [])),
            allowed_topics=set(data.get("allowed_topics", [])),
            enable_pii_filter=data.get("enable_pii_filter", True),
            enable_toxicity_filter=data.get("enable_toxicity_filter", True),
            enable_injection_filter=data.get("enable_injection_filter", True),
            pii_action=data.get("pii_action", "redact"),
            toxicity_action=data.get("toxicity_action", "block"),
            injection_action=data.get("injection_action", "block"),
            custom_blocked_patterns=data.get("custom_blocked_patterns", []),
            custom_allowed_patterns=data.get("custom_allowed_patterns", []),
            max_requests_per_minute=data.get("max_requests_per_minute"),
            max_tokens_per_day=data.get("max_tokens_per_day"),
            violation_action=PolicyAction(data.get("violation_action", "block")),
            alert_on_violation=data.get("alert_on_violation", True),
        )


# Predefined policies
DEFAULT_POLICY = ContentPolicy(
    id="default",
    name="Default Policy",
    description="Standard content safety policy",
    enable_pii_filter=True,
    enable_toxicity_filter=True,
    enable_injection_filter=True,
    pii_action="redact",
    toxicity_action="block",
    injection_action="block",
)

STRICT_POLICY = ContentPolicy(
    id="strict",
    name="Strict Policy",
    description="Maximum content safety with zero tolerance",
    enable_pii_filter=True,
    enable_toxicity_filter=True,
    enable_injection_filter=True,
    pii_action="block",
    toxicity_action="block",
    injection_action="block",
    violation_action=PolicyAction.BLOCK,
)

PERMISSIVE_POLICY = ContentPolicy(
    id="permissive",
    name="Permissive Policy",
    description="Minimal content filtering for trusted use cases",
    enable_pii_filter=False,
    enable_toxicity_filter=True,
    enable_injection_filter=True,
    pii_action="log",
    toxicity_action="flag",
    injection_action="block",
    violation_action=PolicyAction.WARN,
)

RESEARCH_POLICY = ContentPolicy(
    id="research",
    name="Research Policy",
    description="Policy for research and testing environments",
    enable_pii_filter=True,
    enable_toxicity_filter=False,
    enable_injection_filter=True,
    pii_action="redact",
    toxicity_action="log",
    injection_action="block",
    violation_action=PolicyAction.WARN,
)


class PolicyEngine:
    """Engine for evaluating content against policies.
    
    Checks content for policy violations and determines
    appropriate actions.
    """
    
    def __init__(self, policy: ContentPolicy):
        self.policy = policy
    
    def check_content(self, content: str, context: Optional[Dict] = None) -> List[PolicyViolation]:
        """Check content for policy violations.
        
        Args:
            content: The content to check
            context: Optional context about the request
            
        Returns:
            List of policy violations found
        """
        violations: List[PolicyViolation] = []
        
        # Check blocked topics
        for topic in self.policy.blocked_topics:
            if topic.lower() in content.lower():
                violations.append(PolicyViolation(
                    policy_id=self.policy.id,
                    policy_name=self.policy.name,
                    severity=PolicySeverity.HIGH,
                    message=f"Content contains blocked topic: {topic}",
                    details={"topic": topic},
                ))
        
        # Check custom blocked patterns
        import re
        for pattern in self.policy.custom_blocked_patterns:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    violations.append(PolicyViolation(
                        policy_id=self.policy.id,
                        policy_name=self.policy.name,
                        severity=PolicySeverity.MEDIUM,
                        message="Content matches blocked pattern",
                        details={"pattern": pattern},
                    ))
            except re.error:
                logger.warning(f"Invalid regex pattern in policy: {pattern}")
        
        return violations
    
    def get_action(self, violations: List[PolicyViolation]) -> PolicyAction:
        """Determine action based on violations.
        
        Returns the most severe action from all violations.
        """
        if not violations:
            return PolicyAction.ALLOW
        
        # Map severity to action
        severity_order = {
            PolicySeverity.LOW: 1,
            PolicySeverity.MEDIUM: 2,
            PolicySeverity.HIGH: 3,
            PolicySeverity.CRITICAL: 4,
        }
        
        max_severity = max(
            (severity_order.get(v.severity, 0) for v in violations),
            default=0
        )
        
        # Determine action based on severity and policy settings
        if max_severity >= 3:  # HIGH or CRITICAL
            return self.policy.violation_action
        elif max_severity >= 2:  # MEDIUM
            return PolicyAction.WARN if self.policy.violation_action == PolicyAction.ALLOW else self.policy.violation_action
        else:
            return PolicyAction.ALLOW if self.policy.violation_action == PolicyAction.ALLOW else PolicyAction.WARN
