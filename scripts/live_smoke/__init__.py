"""Live smoke suite for a deployed DeltaLLM gateway.

Run it after a deployment or an upgrade. It uses the master key only for admin
setup, then calls every model through a key that a test user creates. See
docs/deployment/live-smoke-suite.md.
"""
