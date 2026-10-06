"""Fixed native reporting signals retain last-known values on lookup failure."""

from prometheus_client import Gauge

from src.metrics.prometheus import get_prometheus_registry

_registry = get_prometheus_registry()
read_model_progress_available = Gauge(
    "deltallm_accounting_read_model_progress_available",
    "Whether the latest native reporting lookup returned all required cells",
    registry=_registry,
)
read_model_observed_timestamp = Gauge(
    "deltallm_accounting_read_model_observed_timestamp_seconds",
    "Wall-clock time of the last actual native reporting observation",
    registry=_registry,
)
read_model_pending_partitions = Gauge(
    "deltallm_accounting_read_model_pending_partitions",
    "Fixed partitions with source events beyond their reporting checkpoints",
    registry=_registry,
)
read_model_oldest_head_age = Gauge(
    "deltallm_accounting_read_model_oldest_head_age_seconds",
    "Oldest unprojected partition head at the last actual observation",
    registry=_registry,
)
native_oldest_work_age = Gauge(
    "deltallm_accounting_native_oldest_work_age_seconds",
    "Oldest observed terminal or reporting work in the native pipeline",
    registry=_registry,
)
native_work_observation_available = Gauge(
    "deltallm_accounting_native_work_observation_available",
    "Whether both native pipeline observations are complete and current",
    registry=_registry,
)
native_work_observed_timestamp = Gauge(
    "deltallm_accounting_native_work_observed_timestamp_seconds",
    "Wall-clock time of the last complete native pipeline observation",
    registry=_registry,
)


def observe_read_model_progress(
    *,
    complete: bool,
    pending_partitions: int,
    oldest_age_seconds: float | None,
) -> None:
    read_model_progress_available.set(int(complete))
    read_model_pending_partitions.set(pending_partitions)
    read_model_oldest_head_age.set(oldest_age_seconds if oldest_age_seconds is not None else 0)
    read_model_observed_timestamp.set_to_current_time()


def unavailable_read_model_progress() -> None:
    # Keep the last values and their timestamp. Failure is not an empty queue.
    read_model_progress_available.set(0)


def observe_native_work_age(
    *, terminal_seconds: float | None, report_seconds: float | None
) -> None:
    native_oldest_work_age.set(max(terminal_seconds or 0, report_seconds or 0))
    native_work_observation_available.set(1)
    native_work_observed_timestamp.set_to_current_time()


def unavailable_native_work_observation() -> None:
    native_work_observation_available.set(0)
