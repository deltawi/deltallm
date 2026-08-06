from __future__ import annotations

import logging
from typing import Any

from src.batch.storage import LocalBatchArtifactStorage, S3BatchArtifactStorage

logger = logging.getLogger(__name__)


def build_s3_batch_storage(cfg: Any) -> S3BatchArtifactStorage:
    general = cfg.general_settings
    bucket = str(getattr(general, "embeddings_batch_s3_bucket", "") or "").strip()
    if not bucket:
        raise RuntimeError(
            "embeddings_batch_s3_bucket must be configured when "
            "embeddings_batch_storage_backend='s3'"
        )
    try:
        return S3BatchArtifactStorage(
            bucket=bucket,
            region=str(
                getattr(general, "embeddings_batch_s3_region", "us-east-1") or "us-east-1"
            ),
            prefix=str(
                getattr(general, "embeddings_batch_s3_prefix", "deltallm/batch-artifacts") or ""
            ),
            endpoint_url=getattr(general, "embeddings_batch_s3_endpoint_url", None),
            access_key_id=getattr(general, "embeddings_batch_s3_access_key_id", None),
            secret_access_key=getattr(general, "embeddings_batch_s3_secret_access_key", None),
            spool_max_bytes=int(
                getattr(general, "embeddings_batch_s3_spool_max_bytes", 8_388_608)
                or 8_388_608
            ),
        )
    except ImportError as exc:
        raise RuntimeError(
            "S3 batch storage requires the 'batch-s3' optional dependency; "
            "install with `pip install .[batch-s3]`"
        ) from exc


def build_batch_storage_registry(cfg: Any) -> dict[str, Any]:
    general = cfg.general_settings
    backend = str(
        getattr(general, "embeddings_batch_storage_backend", "local") or "local"
    ).strip().lower()
    registry: dict[str, Any] = {}
    try:
        registry["local"] = LocalBatchArtifactStorage(general.embeddings_batch_storage_dir)
    except Exception:
        if backend == "local":
            raise
        logger.warning(
            "batch local storage backend unavailable for legacy artifact routing path=%s",
            general.embeddings_batch_storage_dir,
            exc_info=True,
        )
    bucket = str(getattr(general, "embeddings_batch_s3_bucket", "") or "").strip()
    if bucket:
        try:
            registry["s3"] = build_s3_batch_storage(cfg)
        except RuntimeError:
            if backend == "s3":
                raise
    return registry


def build_batch_storage(cfg: Any, storage_registry: dict[str, Any]) -> Any:
    general = cfg.general_settings
    backend = str(
        getattr(general, "embeddings_batch_storage_backend", "local") or "local"
    ).strip().lower()
    storage = storage_registry.get(backend)
    if storage is None:
        if backend == "s3":
            return build_s3_batch_storage(cfg)
        raise RuntimeError(f"Unsupported embeddings batch storage backend: {backend}")
    return storage
