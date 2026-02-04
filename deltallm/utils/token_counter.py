"""Token counting utilities."""

from typing import Optional, Union
import json

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


class TokenCounter:
    """Token counter for various models."""
    
    # Rough estimates for different tokenizers
    CHARS_PER_TOKEN = {
        "default": 4,  # ~4 chars per token
        "claude": 3.5,  # Claude uses ~3.5 chars per token
        "gpt-4": 4,
        "gpt-3.5": 4,
    }
    
    def __init__(self):
        self._encoders: dict[str, any] = {}
    
    def _get_encoder(self, model: str):
        """Get tiktoken encoder for a model."""
        if not TIKTOKEN_AVAILABLE:
            return None
        
        if model not in self._encoders:
            try:
                self._encoders[model] = tiktoken.encoding_for_model(model)
            except KeyError:
                # Try to use cl100k_base as fallback
                try:
                    self._encoders[model] = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    self._encoders[model] = None
        
        return self._encoders[model]
    
    def count_tokens(
        self,
        text: Union[str, list[dict]],
        model: str = "gpt-4",
    ) -> int:
        """Count tokens in text.
        
        Args:
            text: Text or messages to count
            model: Model name
            
        Returns:
            Token count
        """
        # Handle messages
        if isinstance(text, list):
            return self.count_message_tokens(text, model)
        
        # Try tiktoken first
        encoder = self._get_encoder(model)
        if encoder:
            return len(encoder.encode(text))
        
        # Fallback to character-based estimation
        chars_per_token = self.CHARS_PER_TOKEN.get(
            model.split("-")[0], 
            self.CHARS_PER_TOKEN["default"]
        )
        return int(len(text) / chars_per_token) + 1
    
    def count_message_tokens(
        self,
        messages: list[dict],
        model: str = "gpt-4",
    ) -> int:
        """Count tokens in messages including formatting overhead.
        
        Args:
            messages: List of message dictionaries
            model: Model name
            
        Returns:
            Token count
        """
        encoder = self._get_encoder(model)
        
        if not encoder:
            # Fallback to character-based estimation
            total_chars = sum(
                len(json.dumps(msg)) for msg in messages
            )
            return int(total_chars / 4) + len(messages) * 4
        
        # Use tiktoken
        tokens = 0
        
        for message in messages:
            # Every message follows <|start|>{role/name}\n{content}<|end|>\n
            tokens += 4  # Every message has 4 base tokens
            
            for key, value in message.items():
                if value is None:
                    continue
                    
                tokens += len(encoder.encode(key))
                
                if isinstance(value, str):
                    tokens += len(encoder.encode(value))
                elif isinstance(value, list):
                    # Handle content blocks (for vision)
                    for item in value:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                if isinstance(v, str):
                                    tokens += len(encoder.encode(v))
                                else:
                                    tokens += len(encoder.encode(str(v)))
                        else:
                            tokens += len(encoder.encode(str(item)))
                else:
                    tokens += len(encoder.encode(str(value)))
            
            if message.get("name"):
                tokens += -1  # Role is omitted if name is present
        
        tokens += 2  # Every reply is primed with <|start|>assistant<|message|>
        
        return tokens
    
    def estimate_image_tokens(
        self,
        width: int,
        height: int,
        model: str = "gpt-4o",
        detail: str = "auto",
    ) -> int:
        """Estimate token count for an image.
        
        Args:
            width: Image width
            height: Image height
            model: Model name
            detail: Detail level ("low", "high", "auto")
            
        Returns:
            Estimated token count
        """
        if detail == "low":
            return 85  # Fixed cost for low detail
        
        if model.startswith("gpt-4o"):
            # GPT-4o vision pricing
            # Base cost + tiles
            # For high detail, image is scaled to fit in 2048x2048, then tiled into 512x512
            
            # Scale to fit in 2048x2048 while maintaining aspect ratio
            max_dim = 2048
            if width > max_dim or height > max_dim:
                scale = max_dim / max(width, height)
                width = int(width * scale)
                height = int(height * scale)
            
            # Calculate tiles (512x512 each)
            tiles_x = (width + 511) // 512
            tiles_y = (height + 511) // 512
            num_tiles = tiles_x * tiles_y
            
            # Base cost + 170 tokens per tile
            return 85 + (num_tiles * 170)
        
        elif "claude" in model.lower():
            # Claude doesn't charge extra for images in tokens
            # It depends on the image size
            return 1000  # Rough estimate
        
        # Default fallback
        return 1000


# Global token counter instance
token_counter = TokenCounter()


def count_tokens(text: Union[str, list[dict]], model: str = "gpt-4") -> int:
    """Count tokens in text or messages.
    
    Args:
        text: Text or messages
        model: Model name
        
    Returns:
        Token count
    """
    return token_counter.count_tokens(text, model)


def count_message_tokens(messages: list[dict], model: str = "gpt-4") -> int:
    """Count tokens in messages.
    
    Args:
        messages: List of messages
        model: Model name
        
    Returns:
        Token count
    """
    return token_counter.count_message_tokens(messages, model)
