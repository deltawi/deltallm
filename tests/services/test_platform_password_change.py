from __future__ import annotations

import pytest

from src.db.identity.platform_passwords import PlatformPasswordRecord
from src.services.identity.platform_password_change import PlatformPasswordChangeService


class Passwords:
    def __init__(self, stored: str | None, *, exists: bool = True, concurrent: bool = False):
        self.record = PlatformPasswordRecord(stored) if exists else None
        self.concurrent = concurrent
        self.writes = []

    async def get(self, account_id):
        return self.record

    async def replace(self, account_id, *, expected_hash, password_hash):
        self.writes.append((account_id, expected_hash, password_hash))
        return not self.concurrent


@pytest.mark.parametrize("stored", [None, ""])
async def test_external_password_creation_is_denied_without_a_write(stored):
    passwords = Passwords(stored)
    service = PlatformPasswordChangeService(
        passwords,
        hash_password=lambda p: "hash:" + p,
        verify_password=lambda p, h: h == "hash:" + p,
    )
    assert not await service.change(
        account_id="customer",
        new_password="new",
        current_password=None,
        require_existing_password=True,
    )
    assert passwords.writes == []


@pytest.mark.parametrize("current", [None, "wrong"])
async def test_linked_account_password_change_requires_current_password(current):
    passwords = Passwords("hash:old")
    service = PlatformPasswordChangeService(
        passwords,
        hash_password=lambda p: "hash:" + p,
        verify_password=lambda p, h: h == "hash:" + p,
    )
    assert not await service.change(
        account_id="customer",
        new_password="new",
        current_password=current,
        require_existing_password=True,
    )
    assert passwords.writes == []


@pytest.mark.parametrize("require_existing", [False, True])
async def test_existing_password_change_keeps_one_verified_write(require_existing):
    passwords = Passwords("hash:old")
    service = PlatformPasswordChangeService(
        passwords,
        hash_password=lambda p: "hash:" + p,
        verify_password=lambda p, h: h == "hash:" + p,
    )
    assert await service.change(
        account_id="customer",
        new_password="new",
        current_password="old",
        require_existing_password=require_existing,
    )
    assert passwords.writes == [("customer", "hash:old", "hash:new")]


async def test_operator_password_creation_remains_available():
    passwords = Passwords(None)
    service = PlatformPasswordChangeService(
        passwords, hash_password=lambda p: "hash:" + p, verify_password=lambda p, h: False
    )
    assert await service.change(
        account_id="operator",
        new_password="new",
        current_password=None,
        require_existing_password=False,
    )
    assert passwords.writes == [("operator", None, "hash:new")]


@pytest.mark.parametrize("exists,concurrent", [(False, False), (True, True)])
async def test_missing_or_changed_password_state_does_not_report_success(exists, concurrent):
    passwords = Passwords("hash:old", exists=exists, concurrent=concurrent)
    service = PlatformPasswordChangeService(
        passwords,
        hash_password=lambda p: "hash:" + p,
        verify_password=lambda p, h: h == "hash:" + p,
    )
    assert not await service.change(
        account_id="customer",
        new_password="new",
        current_password="old",
        require_existing_password=True,
    )
