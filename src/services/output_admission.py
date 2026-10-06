from __future__ import annotations

from src.models.responses import UserAPIKeyAuth
from src.services.output_limit_types import OutputPolicy, output_scopes


def prepare_output_policy(auth: UserAPIKeyAuth) -> OutputPolicy | None:
    scopes = output_scopes(auth)
    return OutputPolicy(scopes) if scopes else None
