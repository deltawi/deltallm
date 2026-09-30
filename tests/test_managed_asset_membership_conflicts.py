from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from prisma.errors import RawQueryError

from src.api.admin.endpoints.common import managed_asset_membership_transaction


class _AudienceConflictDatabase:
    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        yield object()
        raise RawQueryError(
            {
                "user_facing_error": {
                    "error_code": "P2010",
                    "message": "Raw query failed",
                    "meta": {
                        "code": "23514",
                        "message": (
                            "creator model audience exceeds its named credential audience"
                        ),
                    },
                }
            }
        )


async def test_membership_audience_failure_is_reported_as_conflict() -> None:
    with pytest.raises(HTTPException) as caught:
        async with managed_asset_membership_transaction(_AudienceConflictDatabase()):
            pass

    assert caught.value.status_code == 409
    assert "named credential shared through it" in str(caught.value.detail)
