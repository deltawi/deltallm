"""One startup contract selects native roles before any dependency is opened."""

from src.accounting_settings import AccountingProtocolSettings
from src.deployment_capacity_report import Role
from urllib.parse import urlsplit


def validate_accounting_role(config: AccountingProtocolSettings, role: Role) -> None:
    if config.accounting_execution_mode != "local_journal":
        if role == "accountingRequest":
            raise ValueError("accounting request role requires local journal mode")
        return
    if role == "accountingWorker":
        if not config.accounting_projection_worker_enabled:
            raise ValueError("native projection role requires its projection worker")
        return
    if config.accounting_projection_worker_enabled:
        raise ValueError("native projection must run in its dedicated role")
    if config.accounting_rpc_signing_secret is None:
        raise ValueError("native accounting RPC requires its signing secret")
    if role == "accountingRequest":
        return
    if config.accounting_request_url is None:
        raise ValueError("native API and batch roles require the accounting request origin")
    if not config.accounting_rpc_allowed_private_cidrs:
        raise ValueError("native accounting requires its explicit private network allowlist")
    if config.accounting_request_url.startswith("http:") and not config.accounting_rpc_allow_http:
        raise ValueError("native accounting HTTP requires explicit permission")
    origin = urlsplit(config.accounting_request_url)
    port = origin.port or (443 if origin.scheme == "https" else 80)
    if port not in config.accounting_rpc_allowed_ports:
        raise ValueError("native accounting origin port is outside its allowlist")
