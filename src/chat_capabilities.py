from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ChatRoutingCapabilities(BaseModel):
    """Operator-qualified model capabilities, not inferred from a model name.

    Text chat is the common floor. Optional features default to unsupported. This
    declaration is required for selector members before their policy can activate.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    tools: bool = False
    json_object: bool = False
    json_schema: bool = False
    image: bool = False
    audio: bool = False
    file: bool = False
    streaming: bool = False
    multiple_choices: bool = False
