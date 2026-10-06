"""Native settings keep one role, one money authority, and bounded private RPC."""

import pytest

from src.accounting_settings import AccountingProtocolSettings, ACCOUNTING_PROTOCOL_FIELDS
from src.bootstrap.accounting_role_config import validate_accounting_role
from src.config import GeneralSettings, Settings


def native(**changes):
    return AccountingProtocolSettings(
        **{
            "accounting_protocol_enabled": True,
            "accounting_execution_mode": "local_journal",
            "accounting_request_url": "https://accounting.internal",
            "accounting_rpc_signing_secret": "s" * 32,
            "accounting_rpc_allowed_private_cidrs": ("10.96.0.0/12",),
            **changes,
        }
    )


@pytest.mark.parametrize("role", ["api", "batchWorker", "accountingRequest"])
def test_native_roles_share_one_validated_execution_bundle(role):
    validate_accounting_role(native(), role)


def test_projection_role_does_not_require_the_rpc_signing_secret():
    validate_accounting_role(
        native(accounting_rpc_signing_secret=None, accounting_projection_worker_enabled=True),
        "accountingWorker",
    )


@pytest.mark.parametrize(
    ("role", "changes"),
    [
        ("api", {"accounting_request_url": None}),
        ("api", {"accounting_rpc_signing_secret": None}),
        ("api", {"accounting_rpc_allowed_private_cidrs": ()}),
        ("api", {"accounting_request_url": "http://accounting.internal"}),
        ("api", {"accounting_request_url": "https://accounting.internal:4000"}),
        ("api", {"accounting_projection_worker_enabled": True}),
        ("accountingRequest", {"accounting_rpc_signing_secret": None}),
        ("accountingWorker", {"accounting_projection_worker_enabled": False}),
    ],
)
def test_incomplete_or_ambiguous_native_role_fails_before_opening_clients(role, changes):
    with pytest.raises(ValueError):
        validate_accounting_role(native(**changes), role)


@pytest.mark.parametrize(
    "changes",
    [
        {"accounting_protocol_enabled": False},
        {"accounting_grants_enabled": False},
        {"accounting_projection_poll_interval_ms": 10_000},
        {"accounting_projection_maintenance_interval_ms": 60_000},
        {"accounting_request_url": "https://user:secret@accounting.internal"},
        {"accounting_request_url": "https://accounting.internal/private"},
        {"accounting_request_url": "https://accounting.internal?token=secret"},
        {"accounting_rpc_allowed_private_cidrs": ("0.0.0.0/0",)},
        {"accounting_rpc_allowed_ports": (True,)},
        {"accounting_rpc_signing_secret": "too-short"},
        {"accounting_rpc_max_connections": 257},
    ],
)
def test_native_bounds_and_origin_reject_invalid_configuration(changes):
    with pytest.raises(ValueError):
        native(**changes)


def test_startup_config_surfaces_use_the_same_canonical_fields_and_hide_the_secret():
    assert ACCOUNTING_PROTOCOL_FIELDS <= GeneralSettings.model_fields.keys()
    assert ACCOUNTING_PROTOCOL_FIELDS <= Settings.model_fields.keys()
    config = native()
    assert "s" * 32 not in repr(config)
    assert "s" * 32 not in config.model_dump_json()


def test_staged_native_mode_never_silently_selects_assigned_persistence():
    from src.bootstrap.accounting import start_accounting_protocol

    with pytest.raises(RuntimeError, match="native runtime owner"):
        start_accounting_protocol(native(), client=None, owner_id="owner")
