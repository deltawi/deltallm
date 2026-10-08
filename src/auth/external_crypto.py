from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from src.metrics.external_auth import external_auth_saturation
from src.auth.external_assertions import ExternalAssertionVerifier
from src.auth.external_contracts import ExternalPurpose, VerifiedExternalAssertion
from src.auth.external_errors import ExternalAuthUnavailable, InvalidExternalAssertion


@dataclass(frozen=True, slots=True)
class VerificationWork:
    token: str = field(repr=False)
    purposes: tuple[ExternalPurpose, ...]
    now: int
    result: asyncio.Future[VerifiedExternalAssertion] = field(repr=False)


class ExternalCryptoExecutor:
    """Cancelled requests retain their CPU slot until verification finishes."""

    def __init__(self, verifier: ExternalAssertionVerifier) -> None:
        self.verifier = verifier
        self.queue: asyncio.Queue[VerificationWork | None] = asyncio.Queue(maxsize=8)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="external-auth")
        self.workers: list[asyncio.Task[None]] = []
        self.closed = False

    async def start(self) -> None:
        if self.closed or self.workers:
            raise RuntimeError("External verification executor cannot start twice")
        self.workers = [
            asyncio.create_task(self._run(), name=f"external-auth-crypto-{index}")
            for index in range(2)
        ]

    @property
    def ready(self) -> bool:
        return (
            bool(self.workers) and not self.closed and all(not task.done() for task in self.workers)
        )

    async def verify(
        self, token: str, *, purposes: tuple[ExternalPurpose, ...], now: int
    ) -> VerifiedExternalAssertion:
        if not self.ready:
            raise ExternalAuthUnavailable()
        result: asyncio.Future[VerifiedExternalAssertion] = (
            asyncio.get_running_loop().create_future()
        )
        try:
            self.queue.put_nowait(VerificationWork(token, purposes, now, result))
        except asyncio.QueueFull as exc:
            external_auth_saturation.labels("crypto", "full").inc()
            raise ExternalAuthUnavailable() from exc
        return await result

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            work = await self.queue.get()
            try:
                if work is None:
                    return
                if work.result.cancelled():
                    continue
                try:
                    verified = await loop.run_in_executor(
                        self.executor,
                        lambda: self.verifier.verify(
                            work.token, purposes=work.purposes, now=work.now
                        ),
                    )
                except asyncio.CancelledError:
                    if not work.result.done():
                        work.result.set_exception(ExternalAuthUnavailable())
                    raise
                except InvalidExternalAssertion as exc:
                    if not work.result.done():
                        work.result.set_exception(exc)
                except Exception:
                    if not work.result.done():
                        work.result.set_exception(ExternalAuthUnavailable())
                else:
                    if not work.result.done():
                        work.result.set_result(verified)
            finally:
                self.queue.task_done()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        while not self.queue.empty():
            work = self.queue.get_nowait()
            if work is not None and not work.result.done():
                work.result.set_exception(ExternalAuthUnavailable())
            self.queue.task_done()
        for _ in self.workers:
            self.queue.put_nowait(None)
        try:
            async with asyncio.timeout(1):
                await asyncio.gather(*self.workers)
        except TimeoutError:
            for worker in self.workers:
                worker.cancel()
            await asyncio.gather(*self.workers, return_exceptions=True)
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
