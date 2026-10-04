"""Managed, one-process container entrypoint: ``python -m src.server``."""

from __future__ import annotations

import argparse
import os
import signal
import sys

import uvicorn

from src.managed_server import ManagedServer
from src.process_lifecycle import ProcessLifecycle
from src.shutdown_watchdog import ShutdownWatchdog
from src.startup_config import StartupConfig
from src.config import resolve_database_settings
from src.prisma_bootstrap import run_prisma_bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "4000")))
    args = parser.parse_args(argv)
    if int(os.getenv("WEB_CONCURRENCY", "1")) != 1:
        parser.error("managed containers require WEB_CONCURRENCY=1")
    for handled in (signal.SIGTERM, signal.SIGINT):
        signal.signal(handled, lambda sig, _: sys.exit(128 + sig))
    startup = StartupConfig.load()
    lifecycle = ProcessLifecycle(startup.lifecycle)
    watchdog = ShutdownWatchdog()
    lifecycle.on_drain = watchdog.arm
    for handled in (signal.SIGTERM, signal.SIGINT):
        signal.signal(handled, lambda *_: lifecycle.begin_drain())
    if startup.lifecycle.migration_mode == "startup":

        def interrupt_migration(sig: int, _: object) -> None:
            lifecycle.begin_drain()
            # The subprocess owner kills and reaps its process group on unwind.
            raise SystemExit(128 + sig)

        for handled in (signal.SIGTERM, signal.SIGINT):
            signal.signal(handled, interrupt_migration)
        database = resolve_database_settings(startup.app_config, startup.settings)
        if database is None:
            raise RuntimeError("Startup migration requires an explicit database URL")
        run_prisma_bootstrap(environment={**os.environ, "DATABASE_URL": database.url})
        for handled in (signal.SIGTERM, signal.SIGINT):
            signal.signal(handled, lambda *_: lifecycle.begin_drain())
    if lifecycle.draining:
        return 1
    from src.main import create_app

    app = create_app(startup=startup, lifecycle=lifecycle)
    server = ManagedServer(
        uvicorn.Config(app, host=args.host, port=args.port, workers=1, lifespan="on"), lifecycle
    )
    server.run()
    owner = getattr(app.state, "shutdown_owner", None)
    return 1 if not server.started or (owner is not None and owner.failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
