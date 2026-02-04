"""Batch processing module.

This module provides batch job processing capabilities with 50% discount on pricing.
"""

from .processor import BatchProcessor, BatchProcessingError

__all__ = ["BatchProcessor", "BatchProcessingError"]
