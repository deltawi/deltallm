"""Compatibility names for the shared bounded capacity gate."""

from src.concurrency import (
    BoundedCapacityGate as BoundedFallbackGate,
    CapacityGateFull as FallbackGateFull,
    CapacityGateTimedOut as FallbackGateTimedOut,
)

__all__ = ["BoundedFallbackGate", "FallbackGateFull", "FallbackGateTimedOut"]
