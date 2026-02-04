"""Content filters for detecting and handling sensitive content.

This module provides various content filtering mechanisms:
- PII (Personally Identifiable Information) detection
- Toxicity/profanity detection
- Prompt injection detection
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Pattern
import logging

logger = logging.getLogger(__name__)


class FilterType(str, Enum):
    """Types of content filters."""
    
    PII = "pii"
    TOXICITY = "toxicity"
    PROMPT_INJECTION = "prompt_injection"
    CUSTOM = "custom"


class FilterAction(str, Enum):
    """Actions to take when filter matches."""
    
    BLOCK = "block"  # Block the request entirely
    REDACT = "redact"  # Redact sensitive content
    FLAG = "flag"  # Allow but flag for review
    LOG = "log"  # Just log the match


@dataclass
class FilterMatch:
    """A match found by a content filter."""
    
    filter_type: FilterType
    pattern: str
    matched_text: str
    position: tuple[int, int]
    confidence: float  # 0-1 confidence score
    severity: str  # low, medium, high


@dataclass
class FilterResult:
    """Result of applying content filters."""
    
    allowed: bool
    action: FilterAction
    matches: List[FilterMatch]
    filtered_content: Optional[str] = None
    message: Optional[str] = None


class ContentFilter:
    """Base class for content filters.
    
    Filters analyze content and return matches based on
    predefined patterns or ML models.
    """
    
    def __init__(
        self,
        filter_type: FilterType,
        action: FilterAction = FilterAction.BLOCK,
        confidence_threshold: float = 0.7,
    ):
        self.filter_type = filter_type
        self.action = action
        self.confidence_threshold = confidence_threshold
    
    def filter(self, content: str) -> FilterResult:
        """Apply filter to content.
        
        Args:
            content: The content to filter
            
        Returns:
            FilterResult with matches and recommended action
        """
        raise NotImplementedError


class PIIFilter(ContentFilter):
    """Filter for detecting and handling PII.
    
    Detects:
    - Email addresses
    - Phone numbers
    - Social Security Numbers
    - Credit card numbers
    - IP addresses
    """
    
    # Regex patterns for common PII
    PATTERNS: dict[str, tuple[Pattern, str]] = {
        "email": (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), "high"),
        "phone": (re.compile(r'\b(?:\+?1[-.]?)?\(?([0-9]{3})\)?[-.]?([0-9]{3})[-.]?([0-9]{4})\b'), "high"),
        "ssn": (re.compile(r'\b(\d{3}-\d{2}-\d{4})\b'), "high"),
        "credit_card": (re.compile(r'\b(?:\d[ -]*?){13,16}\b'), "high"),
        "ip_address": (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), "medium"),
    }
    
    def __init__(
        self,
        action: FilterAction = FilterAction.REDACT,
        redaction_char: str = "*",
        patterns: Optional[List[str]] = None,
    ):
        super().__init__(FilterType.PII, action)
        self.redaction_char = redaction_char
        self.enabled_patterns = patterns or list(self.PATTERNS.keys())
    
    def filter(self, content: str) -> FilterResult:
        """Detect and optionally redact PII."""
        matches: List[FilterMatch] = []
        filtered_content = content
        
        for pattern_name in self.enabled_patterns:
            if pattern_name not in self.PATTERNS:
                continue
                
            pattern, severity = self.PATTERNS[pattern_name]
            
            for match in pattern.finditer(content):
                filter_match = FilterMatch(
                    filter_type=FilterType.PII,
                    pattern=pattern_name,
                    matched_text=match.group(),
                    position=(match.start(), match.end()),
                    confidence=0.95,
                    severity=severity,
                )
                matches.append(filter_match)
                
                # Redact if requested
                if self.action == FilterAction.REDACT:
                    redacted = self.redaction_char * len(match.group())
                    filtered_content = filtered_content[:match.start()] + redacted + filtered_content[match.end():]
        
        # Determine if content is allowed
        high_confidence_matches = [m for m in matches if m.confidence >= self.confidence_threshold]
        allowed = not (self.action == FilterAction.BLOCK and high_confidence_matches)
        
        message = None
        if not allowed:
            message = f"Content blocked: {len(high_confidence_matches)} PII elements detected"
        
        return FilterResult(
            allowed=allowed,
            action=self.action,
            matches=matches,
            filtered_content=filtered_content if self.action == FilterAction.REDACT else None,
            message=message,
        )


class ToxicityFilter(ContentFilter):
    """Filter for detecting toxic or inappropriate content.
    
    Uses keyword-based detection for common profanity and
    toxic language patterns.
    """
    
    # Common profanity/toxic keywords (simplified list)
    DEFAULT_KEYWORDS = [
        # Profanity (commented out to avoid content issues - users can customize)
        # "badword1", "badword2",
    ]
    
    def __init__(
        self,
        action: FilterAction = FilterAction.BLOCK,
        keywords: Optional[List[str]] = None,
        case_sensitive: bool = False,
    ):
        super().__init__(FilterType.TOXICITY, action)
        self.keywords = keywords or self.DEFAULT_KEYWORDS
        self.case_sensitive = case_sensitive
    
    def filter(self, content: str) -> FilterResult:
        """Detect toxic content."""
        matches: List[FilterMatch] = []
        content_to_check = content if self.case_sensitive else content.lower()
        
        for keyword in self.keywords:
            check_keyword = keyword if self.case_sensitive else keyword.lower()
            
            # Find all occurrences
            start = 0
            while True:
                pos = content_to_check.find(check_keyword, start)
                if pos == -1:
                    break
                    
                matches.append(FilterMatch(
                    filter_type=FilterType.TOXICITY,
                    pattern=f"keyword:{keyword}",
                    matched_text=content[pos:pos + len(keyword)],
                    position=(pos, pos + len(keyword)),
                    confidence=0.9,
                    severity="high",
                ))
                start = pos + 1
        
        high_confidence_matches = [m for m in matches if m.confidence >= self.confidence_threshold]
        allowed = not (self.action == FilterAction.BLOCK and high_confidence_matches)
        
        message = None
        if not allowed:
            message = "Content blocked: Toxic language detected"
        
        return FilterResult(
            allowed=allowed,
            action=self.action,
            matches=matches,
            message=message,
        )


class PromptInjectionFilter(ContentFilter):
    """Filter for detecting prompt injection attempts.
    
    Detects common patterns used to bypass safety measures
    or manipulate LLM behavior.
    """
    
    # Common prompt injection patterns
    INJECTION_PATTERNS: List[tuple[Pattern, str, str]] = [
        # Ignore previous instructions
        (re.compile(r'ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instruction|prompt|command)', re.I), "high", "ignore_instructions"),
        # Pretend/roleplay patterns
        (re.compile(r'pretend\s+(you\s+are|to\s+be)\s+', re.I), "medium", "pretend"),
        # DAN-style prompts
        (re.compile(r'do\s+anything\s+now|jailbreak|d(?:an|evil|ude)|mode:\s*\w+', re.I), "high", "jailbreak"),
        # System prompt override attempts
        (re.compile(r'system\s*:\s*|\[system\]|\{\s*"role"\s*:\s*"system"\s*\}', re.I), "high", "system_override"),
        # Delimiter manipulation
        (re.compile(r'```\s*ignore|"""\s*system|<!--\s*ignore', re.I), "high", "delimiter_manipulation"),
    ]
    
    def __init__(self, action: FilterAction = FilterAction.BLOCK):
        super().__init__(FilterType.PROMPT_INJECTION, action)
    
    def filter(self, content: str) -> FilterResult:
        """Detect prompt injection attempts."""
        matches: List[FilterMatch] = []
        
        for pattern, severity, pattern_name in self.INJECTION_PATTERNS:
            for match in pattern.finditer(content):
                matches.append(FilterMatch(
                    filter_type=FilterType.PROMPT_INJECTION,
                    pattern=pattern_name,
                    matched_text=match.group(),
                    position=(match.start(), match.end()),
                    confidence=0.85,
                    severity=severity,
                ))
        
        high_confidence_matches = [m for m in matches if m.confidence >= self.confidence_threshold]
        allowed = not (self.action == FilterAction.BLOCK and high_confidence_matches)
        
        message = None
        if not allowed:
            message = "Content blocked: Potential prompt injection detected"
        
        return FilterResult(
            allowed=allowed,
            action=self.action,
            matches=matches,
            message=message,
        )


class CustomPatternFilter(ContentFilter):
    """Filter using user-defined regex patterns.
    
    Allows organizations to define their own content
    filtering rules.
    """
    
    def __init__(
        self,
        patterns: List[tuple[str, Pattern, str]],  # (name, pattern, severity)
        action: FilterAction = FilterAction.BLOCK,
    ):
        super().__init__(FilterType.CUSTOM, action)
        self.patterns = patterns
    
    def filter(self, content: str) -> FilterResult:
        """Apply custom patterns."""
        matches: List[FilterMatch] = []
        
        for pattern_name, pattern, severity in self.patterns:
            for match in pattern.finditer(content):
                matches.append(FilterMatch(
                    filter_type=FilterType.CUSTOM,
                    pattern=pattern_name,
                    matched_text=match.group(),
                    position=(match.start(), match.end()),
                    confidence=0.8,
                    severity=severity,
                ))
        
        high_confidence_matches = [m for m in matches if m.confidence >= self.confidence_threshold]
        allowed = not (self.action == FilterAction.BLOCK and high_confidence_matches)
        
        return FilterResult(
            allowed=allowed,
            action=self.action,
            matches=matches,
            message=f"Custom filter: {len(matches)} matches" if matches else None,
        )
