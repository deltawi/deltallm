"""Offline conversion of other gateways' configuration files into DeltaLLM configuration.

The converters only read a file and write a new file. They do not connect to a database,
a provider, or a running gateway. The generated `model_list` reaches the database through
the existing first-boot seed (`general_settings.model_deployment_bootstrap_from_config`).
"""
