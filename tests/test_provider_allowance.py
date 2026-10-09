import ast
from dataclasses import asdict
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from src.billing.pricing.cost import ModelPricing
from src.billing.charges.provider_allowance import (
    ProviderRequestBounds,
    conservative_provider_allowance,
)
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
from src.billing.pricing.tier_pricing import PricingResolution
from src.models.requests import (
    AudioSpeechRequest,
    ChatCompletionRequest,
    EmbeddingRequest,
    ImageGenerationRequest,
    RerankRequest,
)
from src.telemetry.provider_request_bounds import validated_provider_request_bounds


def pricing(info=None, *, tokens=True):
    return PricingResolution(
        callable_model="test",
        provider_model="test",
        requested_mode="sync",
        source="deployment",
        customer_model_info=info or {"cost_per_request": "0.1"},
        provider_model_info={},
        customer_token_pricing=ModelPricing(
            input_cost_per_token=0.01,
            output_cost_per_token=0.02,
            context_window=100,
            max_output_tokens=20,
        )
        if tokens
        else None,
        provider_token_pricing=None,
    )


def allowance(bounds, *, call_type="completion", price=None):
    return conservative_provider_allowance(
        pricing=price or pricing(),
        model_info={"max_input_tokens": 100, "max_output_tokens": 20},
        call_type=call_type,
        bounds=bounds,
        max_attempts=3,
    )


@pytest.mark.parametrize("choices,expected", [(1, "3.9"), (3, "11.1"), (10, "36.3")])
def test_multiple_completion_choices_fit_reserved_ceiling(choices, expected):
    assert allowance(ProviderRequestBounds(output_items=choices, max_output_tokens=10)) == Decimal(
        expected
    )


def test_declared_output_default_cannot_reduce_a_larger_sent_limit():
    assert allowance(ProviderRequestBounds(max_output_tokens=100)) == Decimal("9.3")


@pytest.mark.parametrize(
    "inputs,expected", [(["first", "second"], 2), ([1, 2], 1), ([[1], [2]], 2)]
)
def test_embedding_bounds_count_inputs_not_tokens(inputs, expected):
    bounds = validated_provider_request_bounds(EmbeddingRequest(model="test", input=inputs))
    assert bounds.input_items == expected
    assert allowance(bounds, call_type="embedding") >= Decimal(expected * 3)


def test_image_count_comes_from_the_validated_request():
    bounds = validated_provider_request_bounds(
        ImageGenerationRequest(model="test", prompt="test", n=4)
    )
    result = allowance(
        bounds,
        call_type="image_generation",
        price=pricing({"output_cost_per_image": "0.5", "cost_per_request": "0.1"}, tokens=False),
    )
    assert result == Decimal("6.3")


def test_speech_bounds_store_only_the_final_character_count():
    bounds = validated_provider_request_bounds(AudioSpeechRequest(model="test", input="0123456789"))
    assert asdict(bounds)["input_characters"] == 10
    assert "0123456789" not in repr(bounds)
    assert allowance(
        bounds,
        call_type="audio_speech",
        price=pricing(
            {"input_cost_per_character": "0.01", "cost_per_request": "0.1"}, tokens=False
        ),
    ) == Decimal("0.6")


def test_rerank_bounds_cover_each_document():
    bounds = validated_provider_request_bounds(
        RerankRequest(model="test", query="test", documents=["one", "two", "three"])
    )
    assert bounds.input_items == 3


@pytest.mark.parametrize(
    "call_type,rate",
    [
        ("audio_speech", "output_cost_per_second"),
        ("audio_speech", "input_cost_per_audio_token"),
        ("audio_transcription", "input_cost_per_second"),
        ("audio_transcription", "input_cost_per_token"),
    ],
)
def test_unenforceable_audio_rates_cannot_dispatch(call_type, rate):
    with pytest.raises(SpendPersistenceUnavailable):
        allowance(
            ProviderRequestBounds(input_characters=10),
            call_type=call_type,
            price=pricing({rate: "0.1"}, tokens=False),
        )


def test_cost_ceiling_does_not_use_the_ambient_decimal_precision():
    with localcontext() as context:
        context.prec = 3
        result = allowance(ProviderRequestBounds(max_output_tokens=100, output_items=3))
    assert result == Decimal("27.3")


def test_all_provider_dispatch_callers_pass_validated_bounds():
    source = Path(__file__).resolve().parents[1] / "src"
    callers = []
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "durable_provider_call"
            ):
                callers.append(path)
                assert "bounds" in {argument.arg for argument in node.keywords}, path
    assert callers


def test_new_accounting_owners_have_small_typed_boundaries():
    source = Path(__file__).resolve().parents[1] / "src"
    for relative in (
        "bootstrap/accounting.py",
        "bootstrap/accounting_config.py",
        "bootstrap/accounting_role_config.py",
        "bootstrap/accounting_remote.py",
        "bootstrap/server_application.py",
        "billing/charges/provider_allowance.py",
        "billing/accounting/accounting_finalization.py",
        "billing/accounting/accounting_admission.py",
        "billing/accounting/accounting_pricing.py",
        "billing/accounting/journal/accounting_terminal_preparation.py",
        "billing/accounting/journal/accounting_turn_proofs.py",
        "billing/charges/realtime_accounting_bounds.py",
        "billing/charges/realtime_billing.py",
        "billing/charges/realtime_native.py",
        "telemetry/cache_accounting.py",
        "billing/accounting/accounting_protocol.py",
        "billing/accounting/reporting/accounting_projection.py",
        "billing/accounting/permits/preissued_permits.py",
        "billing/accounting/permits/accounting_local_leases.py",
        "billing/accounting/permits/accounting_local_receipts.py",
        "billing/accounting/permits/accounting_local_cursors.py",
        "billing/accounting/permits/accounting_local_issue.py",
        "billing/accounting/permits/accounting_local_admission.py",
        "billing/accounting/permits/accounting_local_funding.py",
        "billing/accounting/permits/accounting_local_issuer.py",
        "billing/accounting/permits/accounting_local_returns.py",
        "billing/accounting/accounting_local_runtime.py",
        "billing/accounting/journal/accounting_local_terminal.py",
        "billing/accounting/accounting_local_service.py",
        "billing/accounting/transport/accounting_local_wire.py",
        "billing/accounting/transport/accounting_auth.py",
        "billing/accounting/transport/accounting_http.py",
        "billing/accounting/transport/accounting_rpc_contracts.py",
        "billing/accounting/transport/accounting_remote_leases.py",
        "billing/accounting/transport/accounting_rpc_service.py",
        "billing/accounting/health/accounting_presence.py",
        "billing/accounting/health/accounting_admission_monitor.py",
        "billing/accounting/health/accounting_native_observation.py",
        "billing/accounting/journal/accounting_journal.py",
        "billing/accounting/journal/accounting_journal_claims.py",
        "billing/accounting/journal/accounting_journal_runtime.py",
        "billing/accounting/health/accounting_health.py",
        "billing/accounting/journal/accounting_recovery.py",
        "billing/accounting/reporting/accounting_read_model_claims.py",
        "billing/accounting/health/accounting_read_model_health.py",
        "billing/accounting/health/accounting_projection_observation.py",
        "billing/accounting/reporting/accounting_read_model_runtime.py",
        "metrics/accounting_journal.py",
        "metrics/accounting_read_models.py",
        "billing/accounting/journal/accounting_terminal_receipts.py",
        "billing/accounting/journal/accounting_journal_terminal.py",
        "billing/accounting/accounting_snapshots.py",
        "billing/accounting/accounting_service.py",
        "billing/accounting/durable_microbatch.py",
        "billing/accounting/durable_batch_bytes.py",
        "db/accounting_protocol.py",
        "db/accounting_calls.py",
        "db/accounting_permits.py",
        "db/accounting_batches.py",
        "db/accounting_permit_results.py",
        "db/accounting_local_leases.py",
        "db/accounting_journal.py",
        "db/accounting_journal_worker.py",
        "db/accounting_health.py",
        "db/accounting_recovery.py",
        "db/accounting_presence.py",
        "db/accounting_read_model.py",
        "db/accounting_read_model_queries.py",
        "bootstrap/accounting_local.py",
        "bootstrap/accounting_roles.py",
        "bootstrap/accounting_role_builders.py",
        "bootstrap/accounting_worker_app.py",
        "db/accounting_local_lease_results.py",
        "db/accounting_projection.py",
        "telemetry/spend_operation.py",
    ):
        text = (source / relative).read_text()
        assert len(text.splitlines()) < 500
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno < 80, node.name
            if isinstance(node, ast.Name):
                assert node.id != "Any", relative


def test_chat_bounds_use_the_final_model_values():
    payload = ChatCompletionRequest(
        model="test", messages=[{"role": "user", "content": "test"}], n=2, max_tokens=50
    )
    assert validated_provider_request_bounds(payload) == ProviderRequestBounds(
        output_items=2, max_output_tokens=50
    )
