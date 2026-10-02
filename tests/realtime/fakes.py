import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from src.providers.openai_realtime import resolve_realtime_target
from src.realtime.contracts import AdmittedRealtime


def target(profile="realtime"):
    return resolve_realtime_target(
        {"model": "openai/provider-model", "api_key": "provider-secret"},
        public_model="voice",
        profile=profile,
    )


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.receives = 0

    async def receive_text(self):
        self.receives += 1
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def send_text(self, message):
        self.outgoing.put_nowait(message)


@dataclass
class Permit:
    authorized: list = field(default_factory=list)
    receipts: list = field(default_factory=list)
    health_calls: int = 0
    error: Exception | None = None

    def authorize_client_event(self, event):
        if self.error:
            raise self.error
        self.authorized.append(event)

    async def prepare_upstream(self, upstream):
        return upstream

    async def before_client_event(self, event):
        pass

    async def accept_usage(self, event):
        if self.error:
            raise self.error
        self.receipts.append(event)

    async def check_health(self):
        self.health_calls += 1
        if self.error:
            raise self.error


class Admission:
    ready = True

    def __init__(self, *, profile="realtime"):
        self.permit = Permit()
        self.target = target(profile)
        self.requests = []
        self.closed = 0
        self.error = None

    @asynccontextmanager
    async def admit(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        try:
            yield AdmittedRealtime(self.target, self.permit)
        finally:
            self.closed += 1


class Connector:
    def __init__(self, socket):
        self.socket = socket
        self.opens = 0
        self.closed = 0

    @asynccontextmanager
    async def open(self, target, limits):
        self.opens += 1
        try:
            yield self.socket
        finally:
            self.closed += 1
