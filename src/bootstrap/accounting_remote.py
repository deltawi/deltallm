"""One API owner closes local grants and queues before its dedicated RPC pool."""

from src.accounting_settings import AccountingProtocolSettings
from src.billing.accounting.transport.accounting_http import AccountingHttpTransport
from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.bootstrap.accounting_local import build_api_accounting_runtime
from src.bootstrap.accounting_role_config import validate_accounting_role
from src.deployment_capacity_report import Role
from src.outbound.network_policy import OutboundNetworkPolicy
from src.process_lifecycle import ProcessLifecycle
from src.shutdown import cleanup_deadline, run_cleanup
from src.telemetry.lifecycle import WorkerHealth


class RemoteAccountingOwner:
    def __init__(
        self,
        config: AccountingProtocolSettings,
        lifecycle: ProcessLifecycle,
        *,
        role: Role,
        owner_id: str,
    ) -> None:
        validate_accounting_role(config, role)
        if config.accounting_execution_mode != "local_journal" or role not in {
            "api",
            "batchWorker",
        }:
            raise ValueError("Remote accounting requires a native API or batch role")
        origin, secret = config.accounting_request_url, config.accounting_rpc_signing_secret
        if origin is None or secret is None:
            raise ValueError("Remote accounting origin or signing secret is missing")
        self._lifecycle = lifecycle
        self._closed = False
        self.transport = AccountingHttpTransport(
            service_url=origin,
            signing_secret=secret.get_secret_value(),
            max_connections=config.accounting_rpc_max_connections,
            network_policy=OutboundNetworkPolicy(
                allow_http=config.accounting_rpc_allow_http,
                allowed_ports=config.accounting_rpc_allowed_ports,
                allowed_private_cidrs=config.accounting_rpc_allowed_private_cidrs,
                resolution_timeout_seconds=0.5,
            ),
        )
        self.runtime = build_api_accounting_runtime(
            self.transport,
            config,
            lifecycle,
            owner_id=owner_id,
            max_subjects=config.accounting_local_max_subjects,
        )

    @property
    def service(self) -> LocalAccountingService:
        return self.runtime.local.service

    @property
    def worker_health(self) -> WorkerHealth:
        return self.runtime.worker_health

    async def start(self, *, expires_at: float) -> None:
        if self._closed:
            raise RuntimeError("Remote accounting cannot restart after close")
        await self.runtime.start(expires_at=expires_at)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.runtime.close(
                expires_at=cleanup_deadline(self._lifecycle.settings.lifecycle_worker_drain_seconds)
            )
        finally:
            await run_cleanup(self.transport.close, phase="close")
