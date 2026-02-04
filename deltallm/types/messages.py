"""Message type definitions."""

from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


MessageRole = Literal["system", "user", "assistant", "tool"]
ContentType = Literal["text", "image_url", "image_base64"]


class ContentBlock(BaseModel):
    """Content block for multimodal messages."""
    
    type: ContentType
    text: Optional[str] = None
    image_url: Optional[dict[str, str]] = None
    
    @model_validator(mode="after")
    def validate_content(self) -> "ContentBlock":
        """Validate that content block has appropriate fields."""
        if self.type == "text" and self.text is None:
            raise ValueError("text content must be provided when type='text'")
        if self.type in ("image_url", "image_base64") and self.image_url is None:
            raise ValueError("image_url must be provided for image content")
        return self


class Message(BaseModel):
    """Chat message following OpenAI format."""
    
    role: MessageRole
    content: Union[str, list[ContentBlock], None] = None
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    
    model_config = {"extra": "allow"}
    
    @model_validator(mode="after")
    def validate_message(self) -> "Message":
        """Validate message structure."""
        # Tool messages must have tool_call_id
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages must have tool_call_id")
        
        # Assistant messages with tool_calls should not have content
        if self.role == "assistant" and self.tool_calls and self.content:
            # This is actually allowed in some cases
            pass
            
        return self
    
    @classmethod
    def system(cls, content: str) -> "Message":
        """Create a system message."""
        return cls(role="system", content=content)
    
    @classmethod
    def user(cls, content: str) -> "Message":
        """Create a user message."""
        return cls(role="user", content=content)
    
    @classmethod
    def assistant(cls, content: str) -> "Message":
        """Create an assistant message."""
        return cls(role="assistant", content=content)
    
    @classmethod
    def tool(cls, content: str, tool_call_id: str) -> "Message":
        """Create a tool message."""
        return cls(role="tool", content=content, tool_call_id=tool_call_id)
    
    @classmethod
    def with_image(
        cls, 
        text: str, 
        image_url: str, 
        detail: Literal["auto", "low", "high"] = "auto"
    ) -> "Message":
        """Create a user message with text and image."""
        return cls(
            role="user",
            content=[
                ContentBlock(type="text", text=text),
                ContentBlock(type="image_url", image_url={"url": image_url, "detail": detail})
            ]
        )
