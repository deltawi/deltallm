from __future__ import annotations

import logging
from types import SimpleNamespace

from src.bootstrap.batch_runtime.pool_sizing import (
    completion_outbox_concurrency,
    warn_if_batch_db_pool_is_small,
)
from src.config import GeneralSettings

_NO_OVERRIDE = SimpleNamespace(db_pool_size=None)


def _general(**overrides) -> GeneralSettings:
    return GeneralSettings(
        **{
            "embeddings_batch_worker_enabled": True,
            "embeddings_batch_completion_outbox_worker_enabled": True,
            "embeddings_batch_scheduler_mode": "smart_v1",
            **overrides,
        }
    )


def test_warns_when_running_items_and_outbox_outgrow_the_pool(caplog):
    general = _general(db_pool_size=20, embeddings_batch_worker_concurrency=16)

    with caplog.at_level(logging.WARNING, logger="src.bootstrap.batch_runtime.pool_sizing"):
        needed = warn_if_batch_db_pool_is_small(general, _NO_OVERRIDE)

    assert needed == 27
    assert (
        "batch db_pool_size 20 is smaller than the 27 connections batch work can use at once "
        "(16 running items + 8 completion outbox + 3 background)"
    ) in caplog.text


def test_each_extra_slice_in_flight_counts_one_background_connection():
    one_slice = _general(
        db_pool_size=20,
        embeddings_batch_worker_concurrency=16,
        embeddings_batch_worker_max_slices_in_flight=1,
    )
    four_slices = _general(
        db_pool_size=20,
        embeddings_batch_worker_concurrency=16,
        embeddings_batch_worker_max_slices_in_flight=4,
    )

    assert warn_if_batch_db_pool_is_small(one_slice, _NO_OVERRIDE) == 26
    assert warn_if_batch_db_pool_is_small(four_slices, _NO_OVERRIDE) == 29


def test_fifo_scheduling_never_counts_extra_slices():
    general = _general(
        db_pool_size=26,
        embeddings_batch_worker_concurrency=16,
        embeddings_batch_scheduler_mode="fifo_v1",
        embeddings_batch_worker_max_slices_in_flight=4,
    )

    assert warn_if_batch_db_pool_is_small(general, _NO_OVERRIDE) is None


def test_default_batch_sizing_fits_the_default_pool(caplog):
    with caplog.at_level(logging.WARNING, logger="src.bootstrap.batch_runtime.pool_sizing"):
        assert warn_if_batch_db_pool_is_small(_general(), _NO_OVERRIDE) is None
    assert caplog.text == ""


def test_runtime_db_pool_size_override_is_used():
    general = _general(db_pool_size=20, embeddings_batch_worker_concurrency=4)

    assert warn_if_batch_db_pool_is_small(general, SimpleNamespace(db_pool_size=6)) == 11
    assert warn_if_batch_db_pool_is_small(general, SimpleNamespace(db_pool_size=11)) is None


def test_outbox_is_not_counted_when_its_worker_is_off():
    general = _general(
        db_pool_size=20,
        embeddings_batch_worker_concurrency=16,
        embeddings_batch_completion_outbox_worker_enabled=False,
    )

    assert warn_if_batch_db_pool_is_small(general, _NO_OVERRIDE) is None


def test_no_check_when_the_batch_worker_is_off():
    general = _general(db_pool_size=4, embeddings_batch_worker_concurrency=16)
    general.embeddings_batch_worker_enabled = False

    assert warn_if_batch_db_pool_is_small(general, _NO_OVERRIDE) is None


def test_completion_outbox_concurrency_follows_workers_up_to_eight():
    assert completion_outbox_concurrency(_general(embeddings_batch_worker_concurrency=2)) == 2
    assert completion_outbox_concurrency(_general(embeddings_batch_worker_concurrency=16)) == 8
