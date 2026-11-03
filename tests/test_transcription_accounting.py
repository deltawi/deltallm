"""Accounting v2 reserves a per-second transcription price from the uploaded file."""

from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest

from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from tests.test_accounting_request_path import _AccountingRepository
from tests.test_transcription_duration import wav


@pytest.fixture
async def accounting(test_app):
    repository = _AccountingRepository()
    service = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    service.start()
    test_app.state.accounting_protocol_service = service
    test_app.state.accounting_max_provider_attempts = 3
    test_app.state.spend_tracking_service = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=service
    )
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info.update(mode="audio_transcription", input_cost_per_second=0.001)
    yield repository
    await service.close()


def provider_returning(test_app, duration):
    forms = []

    async def post(url, **kwargs):
        forms.append(kwargs.get("data"))
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"text": "hello", "duration": duration}, request=request)

    test_app.state.http_client.post = post
    return forms


async def transcribe(client, test_app, content, filename="speech.wav"):
    return await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini", "response_format": "json"},
        files={"file": (filename, content, "application/octet-stream")},
    )


async def test_wav_reserves_its_exact_length_and_settles_at_the_real_duration(
    client, test_app, accounting
):
    forms = provider_returning(test_app, 29.5)
    # A tiny sample rate keeps the upload under the test key token limit.
    response = await transcribe(client, test_app, wav(30, rate=400, bits=8))
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "hello"}
    assert forms[0]["response_format"] == "verbose_json"
    assert accounting.reservations[0].allowance == Decimal("0.09")
    (final,) = accounting.finalizations
    assert final.outcome is AccountingOutcome.COMPLETED
    assert final.exact_charge == Decimal("0.0295")


async def test_other_formats_reserve_from_the_file_size(client, test_app, accounting):
    provider_returning(test_app, 12)
    response = await transcribe(client, test_app, b"ID3" + b"\0" * 9_997, "speech.mp3")
    assert response.status_code == 200, response.text
    # 10,000 bytes at 4 kbit/s is 20 s; three provider attempts.
    assert accounting.reservations[0].allowance == Decimal("0.06")
    assert accounting.finalizations[0].exact_charge == Decimal("0.012")


async def test_a_duration_beyond_the_ceiling_is_charged_the_full_reservation(
    client, test_app, accounting
):
    provider_returning(test_app, 1_000)
    response = await transcribe(client, test_app, b"ID3" + b"\0" * 9_997, "speech.mp3")
    assert response.status_code == 200, response.text
    (final,) = accounting.finalizations
    assert final.outcome is AccountingOutcome.COMPLETED
    assert final.exact_charge == Decimal("0.06")
    assert response.headers["x-deltallm-response-cost"] == "0.06"
