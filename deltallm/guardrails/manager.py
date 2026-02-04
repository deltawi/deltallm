"""Guardrails manager for coordinating content filtering.

The GuardrailsManager orchestrates multiple content filters
and policies to provide comprehensive content safety.
"""

import logging
from typing import Dict, List, Optional

from deltallm.guardrails.filters import (
    ContentFilter,
    FilterResult,
    FilterType,
    PIIFilter,
    ToxicityFilter,
    PromptInjectionFilter,
    FilterAction,
)
from deltallm.guardrails.policies import (
    ContentPolicy,
    PolicyEngine,
    PolicyViolation,
    PolicyAction,
    DEFAULT_POLICY,
)

logger = logging.getLogger(__name__)


class GuardrailsResult:
    """Result of guardrails check."""
    
    def __init__(
        self,
        allowed: bool,
        action: str,
        filtered_content: Optional[str] = None,
        violations: Optional[List[PolicyViolation]] = None,
        filter_matches: Optional[List] = None,
        message: Optional[str] = None,
    ):
        self.allowed = allowed
        self.action = action
        self.filtered_content = filtered_content
        self.violations = violations or []
        self.filter_matches = filter_matches or []
        self.message = message
    
    def to_dict(self) -> Dict:
        """Convert result to dictionary."""
        return {
            "allowed": self.allowed,
            "action": self.action,
            "filtered_content": self.filtered_content,
            "violations": [
                {
                    "policy_id": v.policy_id,
                    "policy_name": v.policy_name,
                    "severity": v.severity,
                    "message": v.message,
                    "details": v.details,
                }
                for v in self.violations
            ],
            "message": self.message,
        }


class GuardrailsManager:
    """Manager for content guardrails.
    
    Coordinates content filtering and policy enforcement
    for all requests.
    
    Example:
        ```python
        manager = GuardrailsManager(policy=DEFAULT_POLICY)
        result = await manager.check_content(
            prompt="User input here",
            response="Model response here",
        )
        
        if not result.allowed:
            print(f"Blocked: {result.message}")
        ```
    """
    
    def __init__(
        self,
        policy: Optional[ContentPolicy] = None,
        filters: Optional[List[ContentFilter]] = None,
    ):
        self.policy = policy or DEFAULT_POLICY
        self.policy_engine = PolicyEngine(self.policy)
        
        # Initialize default filters based on policy
        self.filters: Dict[FilterType, ContentFilter] = {}
        
        if filters:
            for f in filters:
                self.filters[f.filter_type] = f
        else:
            self._setup_default_filters()
    
    def _setup_default_filters(self):
        """Set up default filters based on policy settings."""
        if self.policy.enable_pii_filter:
            self.filters[FilterType.PII] = PIIFilter(
                action=FilterAction(self.policy.pii_action)
            )
        
        if self.policy.enable_toxicity_filter:
            self.filters[FilterType.TOXICITY] = ToxicityFilter(
                action=FilterAction(self.policy.toxicity_action)
            )
        
        if self.policy.enable_injection_filter:
            self.filters[FilterType.PROMPT_INJECTION] = PromptInjectionFilter(
                action=FilterAction(self.policy.injection_action)
            )
    
    def check_prompt(self, prompt: str) -> GuardrailsResult:
        """Check a user prompt for violations.
        
        Args:
            prompt: The user's input prompt
            
        Returns:
            GuardrailsResult with check outcome
        """
        return self._check_content(prompt, is_prompt=True)
    
    def check_response(self, response: str) -> GuardrailsResult:
        """Check a model response for violations.
        
        Args:
            response: The model's output
            
        Returns:
            GuardrailsResult with check outcome
        """
        return self._check_content(response, is_prompt=False)
    
    def check_both(self, prompt: str, response: str) -> GuardrailsResult:
        """Check both prompt and response.
        
        Args:
            prompt: The user's input
            response: The model's output
            
        Returns:
            GuardrailsResult with combined check outcome
        """
        prompt_result = self.check_prompt(prompt)
        response_result = self.check_response(response)
        
        # Combine results
        all_violations = prompt_result.violations + response_result.violations
        all_matches = prompt_result.filter_matches + response_result.filter_matches
        
        # If either is blocked, overall is blocked
        allowed = prompt_result.allowed and response_result.allowed
        
        # Use the more restrictive action
        action = prompt_result.action
        if response_result.action in ["block", "quarantine"]:
            action = response_result.action
        elif response_result.action == "warn" and action == "allow":
            action = "warn"
        
        # Combine messages
        messages = []
        if prompt_result.message:
            messages.append(f"Prompt: {prompt_result.message}")
        if response_result.message:
            messages.append(f"Response: {response_result.message}")
        
        # Use filtered content from prompt if redacted
        filtered_prompt = prompt_result.filtered_content or prompt
        filtered_response = response_result.filtered_content or response
        
        return GuardrailsResult(
            allowed=allowed,
            action=action,
            filtered_content=filtered_prompt if is_prompt else filtered_response,
            violations=all_violations,
            filter_matches=all_matches,
            message="; ".join(messages) if messages else None,
        )
    
    def _check_content(self, content: str, is_prompt: bool) -> GuardrailsResult:
        """Internal method to check content."""
        violations: List[PolicyViolation] = []
        filter_matches = []
        filtered_content: Optional[str] = content
        
        # Apply all enabled filters
        for filter_type, filter_obj in self.filters.items():
            try:
                result = filter_obj.filter(filtered_content or content)
                
                if result.matches:
                    filter_matches.extend(result.matches)
                
                if result.filtered_content:
                    filtered_content = result.filtered_content
                
                # If filter blocks, add as violation
                if result.action == FilterAction.BLOCK and result.matches:
                    violations.append(PolicyViolation(
                        policy_id=self.policy.id,
                        policy_name=self.policy.name,
                        severity="high",
                        message=result.message or f"{filter_type.value} filter triggered",
                        details={"filter_type": filter_type.value, "matches": len(result.matches)},
                    ))
                    
            except Exception as e:
                logger.error(f"Filter {filter_type} failed: {e}")
        
        # Apply policy checks
        policy_violations = self.policy_engine.check_content(content)
        violations.extend(policy_violations)
        
        # Determine final action
        final_action = self.policy_engine.get_action(violations)
        
        # Determine if allowed
        allowed = final_action in [PolicyAction.ALLOW, PolicyAction.WARN]
        
        # Build message
        message = None
        if violations:
            messages = [v.message for v in violations[:3]]  # Top 3 violations
            message = "; ".join(messages)
            if len(violations) > 3:
                message += f" (+{len(violations) - 3} more)"
        
        return GuardrailsResult(
            allowed=allowed,
            action=final_action.value,
            filtered_content=filtered_content if filtered_content != content else None,
            violations=violations,
            filter_matches=filter_matches,
            message=message,
        )
    
    def update_policy(self, policy: ContentPolicy):
        """Update the active policy and reconfigure filters."""
        self.policy = policy
        self.policy_engine = PolicyEngine(policy)
        self.filters.clear()
        self._setup_default_filters()
        logger.info(f"Updated guardrails policy to: {policy.name}")


# Convenience function for quick guardrails checks
def check_content(
    content: str,
    policy: Optional[ContentPolicy] = None,
) -> GuardrailsResult:
    """Quick check content with default or provided policy.
    
    Args:
        content: Content to check
        policy: Optional policy to use (uses default if not provided)
        
    Returns:
        GuardrailsResult
    """
    manager = GuardrailsManager(policy=policy)
    return manager.check_prompt(content)
