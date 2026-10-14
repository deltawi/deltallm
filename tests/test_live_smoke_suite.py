from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from scripts.live_smoke.cli import main
from scripts.live_smoke.probes import silent_wav

MASTER_KEY = "sk-master-test-value"
BASE_URL = "http://gateway.test"


@dataclass
class FakeTier:
    tier_id: str
    tier_key: str
    active_version_id: str | None = None
    versions: dict[str, dict[str, Any]] = field(default_factory=dict)


class FakeGateway:
    """In-memory stand-in for the admin and inference APIs the suite calls."""

    def __init__(self) -> None:
        self.catalog: list[dict[str, Any]] = [
            {"callable_key": "chat-a", "target_type": "model", "mode": "chat"},
            {"callable_key": "chat-group", "target_type": "route_group", "mode": "chat"},
            {"callable_key": "embed-a", "target_type": "model", "mode": "embedding"},
            {"callable_key": "tts-a", "target_type": "model", "mode": "audio_speech"},
            {"callable_key": "stt-a", "target_type": "model", "mode": "audio_transcription"},
            {"callable_key": "image-a", "target_type": "model", "mode": "image_generation"},
            {"callable_key": "rerank-a", "target_type": "model", "mode": "rerank"},
            {"callable_key": "voice-live", "target_type": "model", "mode": "realtime"},
        ]
        self.failing: set[str] = set()
        # Creator models shared with one other organization, or with everyone.
        self.creator_scoped: set[str] = set()
        self.creator_public: set[str] = set()
        self.callable_targets_empty = False
        self.stream_without_done: set[str] = set()
        self.batch_never_finishes = False
        self.tiers: dict[str, FakeTier] = {}
        self.activations = 0
        self.orgs: dict[str, dict[str, Any]] = {}
        self.tombstones: set[str] = set()
        self.deletion_enabled = True
        self.assignments: dict[str, list[dict[str, Any]]] = {}
        self.org_grants: dict[str, list[str]] = {}
        self.teams: dict[str, dict[str, Any]] = {}
        self.team_creates = 0
        self.accounts: dict[str, dict[str, Any]] = {}
        self.org_members: set[tuple[str, str]] = set()
        self.team_members: set[tuple[str, str]] = set()
        self.sessions: dict[str, str] = {}
        self.keys: dict[str, dict[str, Any]] = {}
        self.files: dict[str, str] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.cancelled: list[str] = []
        self.counter = 0

    # helpers -------------------------------------------------------------
    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def keys_allowed(self) -> set[str]:
        allowed: set[str] = set()
        for tier in self.tiers.values():
            if tier.active_version_id:
                version = tier.versions[tier.active_version_id]
                allowed |= {
                    p["callable_key"]
                    for p in version["policies"].values()
                    if p["enabled"] and p["access_mode"] == "allow"
                }
        return allowed

    def _access(self, name: str) -> dict[str, Any]:
        if name in self.creator_scoped:
            grant = {"subject_type": "organization", "subject_id": "org-other"}
            return {"governance_source": "creator", "visibility": "organization", "grants": [grant]}
        if name in self.creator_public:
            grant = {"subject_type": "public", "subject_id": None}
            return {"governance_source": "creator", "visibility": "public", "grants": [grant]}
        return {"governance_source": "platform", "visibility": "private", "grants": []}

    def _bump(self, version: dict[str, Any]) -> dict[str, Any]:
        version["configuration_revision"] += 1
        return {"configuration_revision": version["configuration_revision"]}

    # dispatch ------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/"):
            return self._inference(request, path)
        if path == "/health/liveliness":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/auth/internal/login":
            return self._login(request)
        if path == "/ui/api/keys" and request.method == "POST":
            return self._create_key(request)
        if request.headers.get("authorization") != f"Bearer {MASTER_KEY}":
            return httpx.Response(401, json={"detail": "Invalid API key"})
        return self._admin(request, path)

    def _body(self, request: httpx.Request) -> dict[str, Any]:
        return json.loads(request.content or b"{}")

    def _login(self, request: httpx.Request) -> httpx.Response:
        body = self._body(request)
        account = self.accounts.get(body["email"])
        if account is None or account["password"] != body["password"]:
            return httpx.Response(401, json={"detail": "Invalid credentials or MFA code"})
        token = self._id("psk")
        self.sessions[token] = account["account_id"]
        response = httpx.Response(
            200,
            json={"account_id": account["account_id"], "mfa_required": False},
            headers={"set-cookie": f"deltallm_session={token}; Path=/; HttpOnly; Secure"},
        )
        return response

    def _create_key(self, request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie", "")
        match = re.search(r"deltallm_session=([^;]+)", cookie)
        account_id = self.sessions.get(match.group(1)) if match else None
        if account_id is None:
            return httpx.Response(401, json={"detail": "Authentication required"})
        body = self._body(request)
        if (account_id, body["team_id"]) not in self.team_members:
            return httpx.Response(403, json={"detail": "not a team member"})
        if not body.get("expires"):
            return httpx.Response(400, json={"detail": "expires is required"})
        token = self._id("hash")
        raw = f"sk-user-{token}"
        self.keys[token] = {"raw": raw, "key_name": body["key_name"], "team_id": body["team_id"]}
        return httpx.Response(200, json={"token": token, "raw_key": raw})

    def _admin(self, request: httpx.Request, path: str) -> httpx.Response:
        method = request.method
        query = parse_qs(request.url.query.decode())
        if path == "/ui/api/callable-targets" and self.callable_targets_empty:
            return httpx.Response(200, json={"data": [], "pagination": {"has_more": False}})
        if path == "/ui/api/models":
            data = [
                {
                    "model_name": item["callable_key"],
                    "model_info": {"mode": item["mode"]},
                    "access": self._access(item["callable_key"]),
                }
                for item in self.catalog
                if item["target_type"] == "model"
            ]
            return httpx.Response(200, json={"data": data, "pagination": {"has_more": False}})
        if path == "/ui/api/route-groups":
            data = [
                {"group_key": item["callable_key"], "mode": item["mode"], "enabled": True}
                for item in self.catalog
                if item["target_type"] == "route_group"
            ]
            return httpx.Response(200, json={"data": data, "pagination": {"has_more": False}})
        if path == "/ui/api/callable-targets":
            return httpx.Response(
                200, json={"data": self.catalog, "pagination": {"has_more": False}}
            )
        if path.startswith("/ui/api/tiers"):
            return self._tiers(request, method, path)
        if path.startswith("/ui/api/organizations"):
            return self._orgs(request, method, path)
        if path.startswith("/ui/api/teams"):
            return self._teams(request, method, path)
        if path == "/ui/api/rbac/accounts" and method == "POST":
            body = self._body(request)
            account = self.accounts.setdefault(
                body["email"], {"account_id": self._id("acct"), "email": body["email"]}
            )
            account["password"] = body["password"]
            return httpx.Response(200, json={"account_id": account["account_id"]})
        if path == "/ui/api/rbac/accounts" and method == "GET":
            return httpx.Response(200, json=list(self.accounts.values()))
        if path.startswith("/ui/api/rbac/accounts/") and method == "DELETE":
            account_id = path.rsplit("/", 1)[1]
            self.accounts = {
                k: v for k, v in self.accounts.items() if v["account_id"] != account_id
            }
            return httpx.Response(200, json={"deleted": True})
        if path == "/ui/api/rbac/organization-memberships":
            body = self._body(request)
            self.org_members.add((body["account_id"], body["organization_id"]))
            return httpx.Response(200, json={})
        if path == "/ui/api/keys" and method == "GET":
            team_id = query.get("team_id", [None])[0]
            data = [
                {"token": token, "key_name": key["key_name"]}
                for token, key in self.keys.items()
                if key["team_id"] == team_id
            ]
            return httpx.Response(200, json={"data": data})
        if path.startswith("/ui/api/keys/") and method == "DELETE":
            existed = self.keys.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(200 if existed else 404, json={})
        return httpx.Response(404, json={"detail": f"no fake route {method} {path}"})

    def _tier_payload(self, tier: FakeTier) -> dict[str, Any]:
        drafts = [v for v in tier.versions.values() if v["status"] == "draft"]
        return {
            "tier_id": tier.tier_id,
            "tier_key": tier.tier_key,
            "active_version_id": tier.active_version_id,
            "latest_draft_version": drafts[-1] if drafts else None,
        }

    def _tiers(self, request: httpx.Request, method: str, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")[2:]
        if parts == ["tiers"] and method == "GET":
            data = [self._tier_payload(t) for t in self.tiers.values()]
            return httpx.Response(200, json={"data": data})
        if parts == ["tiers"] and method == "POST":
            body = self._body(request)
            tier = FakeTier(self._id("tier"), body["tier_key"])
            self.tiers[tier.tier_id] = tier
            return httpx.Response(200, json=self._tier_payload(tier))
        tier = self.tiers.get(parts[1])
        if tier is None:
            return httpx.Response(404, json={"detail": "Tier not found"})
        if len(parts) == 2 and method == "GET":
            return httpx.Response(200, json={"tier": self._tier_payload(tier)})
        if len(parts) == 2 and method == "DELETE":
            if any(
                a["tier_id"] == tier.tier_id for items in self.assignments.values() for a in items
            ):
                return httpx.Response(
                    409, json={"detail": "Tier has active organization assignments"}
                )
            del self.tiers[tier.tier_id]
            return httpx.Response(200, json={"deleted": True})
        if parts[2:] == ["versions"] and method == "POST":
            version = {
                "tier_version_id": self._id("ver"),
                "status": "draft",
                "configuration_revision": 0,
                "policies": {},
            }
            tier.versions[version["tier_version_id"]] = version
            return httpx.Response(200, json={k: v for k, v in version.items() if k != "policies"})
        version = tier.versions[parts[3]]
        rest = parts[4:]
        body = self._body(request) if method != "GET" else {}
        if rest == ["activate"]:
            if body["expected_revision"] != version["configuration_revision"]:
                return httpx.Response(409, json={"detail": "stale"})
            if body["expected_active_version_id"] != tier.active_version_id:
                return httpx.Response(409, json={"detail": "active changed"})
            if tier.active_version_id:
                tier.versions[tier.active_version_id]["status"] = "superseded"
            version["status"] = "active"
            tier.active_version_id = version["tier_version_id"]
            self.activations += 1
            return httpx.Response(200, json={"tier_version_id": version["tier_version_id"]})
        if rest[:1] != ["model-policies"]:
            return httpx.Response(404, json={})
        if method == "GET":
            data = list(version["policies"].values())
            return httpx.Response(200, json={"data": data, "pagination": {"has_more": False}})
        if version["status"] != "draft":
            return httpx.Response(409, json={"detail": "not a draft"})
        if body["expected_revision"] != version["configuration_revision"]:
            return httpx.Response(409, json={"detail": "stale revision"})
        if method == "POST":
            policy = {
                "tier_model_policy_id": self._id("pol"),
                "callable_key": body["callable_key"],
                "enabled": True,
                "access_mode": body.get("access_mode", "allow"),
            }
            version["policies"][policy["tier_model_policy_id"]] = policy
            return httpx.Response(200, json={"data": policy, **self._bump(version)})
        policy_id = rest[1]
        if method == "DELETE":
            version["policies"].pop(policy_id)
            return httpx.Response(200, json={"deleted": True, **self._bump(version)})
        version["policies"][policy_id].update(
            {k: v for k, v in body.items() if k in {"enabled", "access_mode"}}
        )
        return httpx.Response(200, json={"data": {}, **self._bump(version)})

    def _orgs(self, request: httpx.Request, method: str, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")[2:]
        if parts == ["organizations"] and method == "GET":
            search = parse_qs(request.url.query.decode()).get("search", [""])[0]
            data = [org for org_id, org in self.orgs.items() if search in org_id]
            return httpx.Response(200, json={"data": data})
        if parts == ["organizations"] and method == "POST":
            body = self._body(request)
            org_id = body["organization_id"]
            if org_id in self.tombstones:
                # The real gateway answers 500 when a write fence rejects a deleted ID.
                return httpx.Response(500, json={"detail": "Internal server error"})
            self.orgs[org_id] = {
                "organization_id": org_id,
                "organization_name": body["organization_name"],
                "lifecycle_state": "active",
            }
            self.assignments[org_id] = [
                {
                    "assignment_id": self._id("asg"),
                    "tier_id": body["primary_tier"]["tier_id"],
                    "tier_version_id": None,
                    "assignment_type": "primary",
                    "enabled": True,
                }
            ]
            return httpx.Response(200, json=self.orgs[org_id])
        org = self.orgs.get(parts[1])
        if org is None:
            return httpx.Response(404, json={"detail": "Organization not found"})
        rest = parts[2:]
        if not rest:
            return httpx.Response(200, json=org)
        if rest[:1] == ["tier-assignments"] and method == "DELETE":
            assignments = self.assignments[org["organization_id"]]
            assignments[:] = [a for a in assignments if a["assignment_id"] != rest[1]]
            return httpx.Response(200, json={"deleted": True})
        if rest == ["tier-assignments"] and method == "POST":
            body = self._body(request)
            assignment = {
                "assignment_id": self._id("asg"),
                "tier_id": body["tier_id"],
                "tier_version_id": None,
                "assignment_type": body["assignment_type"],
                "enabled": True,
            }
            self.assignments[org["organization_id"]].append(assignment)
            return httpx.Response(200, json=assignment)
        if rest == ["tier-assignments"] and method == "GET":
            return httpx.Response(200, json={"data": self.assignments[org["organization_id"]]})
        if rest == ["asset-access"]:
            self.org_grants[org["organization_id"]] = self._body(request)["selected_callable_keys"]
            return httpx.Response(200, json={})
        if rest == ["deletion-plan"]:
            return httpx.Response(
                200,
                json={
                    "organization_name": org["organization_name"],
                    "deletion_job_id": None,
                    "requests_enabled": self.deletion_enabled,
                    "can_request": self.deletion_enabled,
                    "plan_token": "a" * 64,
                    "blocking_dependencies": [],
                },
            )
        if rest == ["deletion-requests"]:
            assert self._body(request)["acknowledge_running_work_cancellation"] is True
            return httpx.Response(202, json={"deletion_job_id": "job-1", "status": "pending"})
        if rest[-1:] == ["expedite"]:
            return httpx.Response(202, json={"deletion_job_id": "job-1", "status": "pending"})
        if rest[:1] == ["deletion-requests"] and method == "GET":
            org_id = org["organization_id"]
            del self.orgs[org_id]
            self.tombstones.add(org_id)
            self.assignments.pop(org_id, None)
            for team_id in [
                t for t, team in self.teams.items() if team["organization_id"] == org_id
            ]:
                del self.teams[team_id]
            return httpx.Response(200, json={"status": "completed", "phase": "completed"})
        return httpx.Response(404, json={})

    def _teams(self, request: httpx.Request, method: str, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")[2:]
        if parts == ["teams"] and method == "POST":
            body = self._body(request)
            if body["team_id"] in self.teams:
                return httpx.Response(500, json={"detail": "duplicate key"})
            self.teams[body["team_id"]] = body
            self.team_creates += 1
            return httpx.Response(200, json=body)
        team = self.teams.get(parts[1])
        if team is None:
            return httpx.Response(404, json={"detail": "Team not found"})
        if method == "DELETE":
            del self.teams[parts[1]]
            return httpx.Response(200, json={"deleted": True})
        if parts[2:] == ["members"]:
            body = self._body(request)
            self.team_members.add((body["user_id"], team["team_id"]))
            return httpx.Response(200, json={})
        return httpx.Response(200, json=team)

    def _inference(self, request: httpx.Request, path: str) -> httpx.Response:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not any(key["raw"] == bearer for key in self.keys.values()):
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})
        allowed = self.keys_allowed() - self.creator_scoped
        if path == "/v1/models":
            data = [{"id": key} for key in sorted(allowed)]
            return httpx.Response(200, json={"object": "list", "data": data})
        if path.startswith("/v1/files"):
            return self._files(request, path)
        if path.startswith("/v1/batches"):
            return self._batches(request, path)
        if request.headers.get("content-type", "").startswith("multipart/"):
            content = request.read()
            model = re.search(rb'name="model"\r\n\r\n([^\r]+)', content).group(1).decode()
        else:
            body = json.loads(request.content)
            model = body["model"]
        if model not in allowed:
            return httpx.Response(403, json={"error": {"message": "not allowed for this key"}})
        if model in self.failing:
            return httpx.Response(503, json={"error": {"message": "upstream down"}})
        if path == "/v1/chat/completions":
            if body.get("stream"):
                chunk = json.dumps({"choices": [{"delta": {"content": "ok"}}]})
                text = f"data: {chunk}\n\n"
                if model not in self.stream_without_done:
                    text += "data: [DONE]\n\n"
                return httpx.Response(
                    200, content=text.encode(), headers={"content-type": "text/event-stream"}
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        if path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        if path == "/v1/audio/speech":
            return httpx.Response(200, content=b"ID3audio")
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": ""})
        if path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"b64_json": "iVBOR"}]})
        if path == "/v1/rerank":
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})
        return httpx.Response(404, json={})

    def _files(self, request: httpx.Request, path: str) -> httpx.Response:
        if request.method == "POST":
            content = request.read()
            start = content.index(b"\r\n\r\n", content.index(b'filename="')) + 4
            end = content.index(b"\r\n--", start)
            file_id = self._id("file")
            self.files[file_id] = content[start:end].decode()
            return httpx.Response(200, json={"id": file_id})
        file_id = path.split("/")[3]
        return httpx.Response(200, content=self.files[file_id].encode())

    def _batches(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/v1/batches":
            body = json.loads(request.content)
            batch_id = self._id("batch")
            self.batches[batch_id] = {
                "id": batch_id,
                "status": "validating",
                "endpoint": body["endpoint"],
                "input_file_id": body["input_file_id"],
            }
            return httpx.Response(200, json=self.batches[batch_id])
        batch_id = path.split("/")[3]
        batch = self.batches[batch_id]
        if path.endswith("/cancel"):
            self.cancelled.append(batch_id)
            batch["status"] = "cancelling"
            return httpx.Response(200, json=batch)
        if not self.batch_never_finishes and batch["status"] == "validating":
            rows = [json.loads(line) for line in self.files[batch["input_file_id"]].splitlines()]
            output = "".join(
                json.dumps(
                    {"custom_id": row["custom_id"], "response": {"status_code": 200}, "error": None}
                )
                + "\n"
                for row in rows
            )
            output_id = self._id("file")
            self.files[output_id] = output
            batch.update(
                status="completed",
                output_file_id=output_id,
                request_counts={"total": len(rows), "completed": len(rows), "failed": 0},
            )
        return httpx.Response(200, json=batch)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def run_suite(gateway: Callable[[httpx.Request], httpx.Response], *args: str) -> tuple[int, str]:
    out = io.StringIO()
    clock = FakeClock()
    code = main(
        ["--base-url", BASE_URL, *args],
        env={"DELTALLM_MASTER_KEY": f"{MASTER_KEY}\n"},
        out=out,
        transport=httpx.MockTransport(gateway),
        clock=clock,
        wait=clock.sleep,
    )
    return code, out.getvalue()


def test_full_run_passes_and_leaves_no_key() -> None:
    gateway = FakeGateway()

    code, output = run_suite(gateway)

    assert code == 0, output
    assert "Result: PASSED" in output
    every_key = {item["callable_key"] for item in gateway.catalog}
    assert gateway.keys_allowed() == every_key
    [grants] = gateway.org_grants.values()
    assert grants == sorted(every_key)
    assert gateway.keys == {}
    assert "SKIP  voice-live" in output
    assert "PASS batch: chat batch on chat-a" in output
    assert "PASS batch: embedding batch on embed-a" in output
    assert MASTER_KEY not in output


def test_empty_callable_target_list_falls_back_to_model_and_route_group_lists() -> None:
    gateway = FakeGateway()
    gateway.callable_targets_empty = True

    code, output = run_suite(gateway, "--no-batch")

    assert code == 0, output
    assert "8 models and route groups from model and route group lists" in output
    assert gateway.keys_allowed() == {item["callable_key"] for item in gateway.catalog}
    assert "SKIP  voice-live" in output


def test_second_run_reuses_resources_and_tier_version() -> None:
    gateway = FakeGateway()
    assert run_suite(gateway)[0] == 0

    code, output = run_suite(gateway)

    assert code == 0, output
    assert gateway.activations == 1
    assert gateway.team_creates == 1
    assert "already allows every model" in output
    assert re.search(r"organization: live-smoke-org-[0-9a-f]{6} reused", output)
    assert len(gateway.orgs) == 1


def test_new_model_is_added_to_the_tier_on_the_next_run() -> None:
    gateway = FakeGateway()
    assert run_suite(gateway)[0] == 0
    gateway.catalog.append({"callable_key": "chat-b", "target_type": "model", "mode": "chat"})

    code, output = run_suite(gateway)

    assert code == 0, output
    assert gateway.activations == 2
    assert "chat-b" in gateway.keys_allowed()
    assert "+1 -0 models" in output


def test_stale_key_from_an_interrupted_run_is_removed() -> None:
    gateway = FakeGateway()
    assert run_suite(gateway)[0] == 0
    gateway.keys["old"] = {
        "raw": "sk-old",
        "key_name": "live-smoke-key",
        "team_id": next(iter(gateway.teams)),
    }

    code, output = run_suite(gateway)

    assert code == 0, output
    assert "remove old test keys: 1 removed" in output
    assert gateway.keys == {}


def test_failing_model_fails_the_run_and_still_deletes_the_key() -> None:
    gateway = FakeGateway()
    gateway.failing.add("embed-a")

    code, output = run_suite(gateway)

    assert code == 1
    assert "Result: FAILED" in output
    assert "FAIL  embed-a" in output
    assert "upstream down" in output
    assert "SKIP batch: embedding batch" in output or "embedding batch: no working model" in output
    assert gateway.keys == {}


def test_stream_without_done_marker_fails() -> None:
    gateway = FakeGateway()
    gateway.stream_without_done.add("chat-group")

    code, output = run_suite(gateway, "--no-batch")

    assert code == 1
    assert "chat stream: stream ended without [DONE]" in output


def test_batch_that_never_finishes_is_cancelled() -> None:
    gateway = FakeGateway()
    gateway.batch_never_finishes = True

    code, output = run_suite(gateway, "--model", "chat-a", "--batch-timeout", "10")

    assert code == 1
    assert gateway.cancelled
    assert "cancelled" in output


def test_model_filter_and_skip_mode() -> None:
    gateway = FakeGateway()

    code, output = run_suite(
        gateway, "--model", "chat-a,image-a,missing-model", "--skip-mode", "image_generation"
    )

    assert code == 1
    assert "FAIL  missing-model" in output
    assert "SKIP  image-a" in output
    assert "tts-a" not in output.split("Model results")[1]
    # The tier still covers every model, not just the filtered ones.
    assert "tts-a" in gateway.keys_allowed()


def test_raw_key_is_not_printed_or_written(tmp_path) -> None:
    gateway = FakeGateway()
    raw_keys: list[str] = []
    original = gateway._create_key

    def capture(request: httpx.Request) -> httpx.Response:
        response = original(request)
        raw_keys.append(response.json()["raw_key"])
        return response

    gateway._create_key = capture  # type: ignore[method-assign]
    report = tmp_path / "report.json"

    code, output = run_suite(gateway, "--json-output", str(report))

    assert code == 0
    text = report.read_text()
    assert json.loads(text)["passed"] is True
    for secret in [*raw_keys, MASTER_KEY]:
        assert secret not in output
        assert secret not in text


def test_cleanup_removes_organization_user_and_tier() -> None:
    gateway = FakeGateway()

    code, output = run_suite(gateway, "--cleanup", "--no-batch")

    assert code == 0, output
    assert gateway.orgs == {}
    assert gateway.accounts == {}
    assert gateway.tiers == {}
    assert gateway.teams == {}


def test_run_after_cleanup_creates_a_new_organization_id() -> None:
    gateway = FakeGateway()
    assert run_suite(gateway, "--cleanup", "--no-batch")[0] == 0
    [deleted] = gateway.tombstones

    code, output = run_suite(gateway, "--no-batch")

    assert code == 0, output
    [current] = gateway.orgs
    assert current != deleted
    assert current.startswith("live-smoke-org-")


def test_cleanup_when_organization_deletion_is_turned_off() -> None:
    gateway = FakeGateway()
    gateway.deletion_enabled = False

    code, output = run_suite(gateway, "--cleanup", "--no-batch")

    assert code == 0, output
    assert "organization deletion is turned off" in output
    [org_id] = gateway.orgs
    assert gateway.assignments[org_id] == []
    assert gateway.teams == {}
    assert gateway.tiers == {}
    assert gateway.accounts == {}

    code, output = run_suite(gateway, "--no-batch")

    assert code == 0, output
    assert list(gateway.orgs) == [org_id]
    assert len(gateway.assignments[org_id]) == 1


def test_cleanup_only_on_empty_gateway() -> None:
    gateway = FakeGateway()

    code, output = run_suite(gateway, "--cleanup-only")

    assert code == 0, output
    assert "organization: not present" in output


def test_missing_master_key_exits_2() -> None:
    out = io.StringIO()
    assert main(["--base-url", BASE_URL], env={}, out=out) == 2


def test_master_key_can_be_typed_at_the_prompt() -> None:
    gateway = FakeGateway()
    out = io.StringIO()
    prompts: list[str] = []

    def prompt(text: str) -> str:
        prompts.append(text)
        return f"  {MASTER_KEY}  "

    code = main(
        ["--base-url", BASE_URL, "--no-batch"],
        env={},
        out=out,
        transport=httpx.MockTransport(gateway),
        prompt=prompt,
    )

    assert code == 0, out.getvalue()
    assert prompts == ["Master key (input is hidden): "]
    assert MASTER_KEY not in out.getvalue()


def test_bad_prefix_exits_2() -> None:
    out = io.StringIO()
    code = main(
        ["--base-url", BASE_URL, "--prefix", "Bad Prefix!"],
        env={"DELTALLM_MASTER_KEY": MASTER_KEY},
        out=out,
    )
    assert code == 2


def test_silent_wav_is_a_valid_wave_file() -> None:
    data = silent_wav(0.1)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"


@pytest.mark.parametrize("missing_access", ["chat-a"])
def test_model_missing_from_key_listing_fails_setup_check(missing_access: str) -> None:
    gateway = FakeGateway()
    original = gateway.keys_allowed

    def without() -> set[str]:
        return original() - {missing_access}

    gateway.keys_allowed = without  # type: ignore[method-assign]

    code, output = run_suite(gateway, "--no-batch")

    assert code == 1
    assert "key sees models: missing: chat-a" in output


def test_network_error_during_cleanup_still_runs_the_other_steps() -> None:
    gateway = FakeGateway()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deletion-plan"):
            raise httpx.ReadTimeout("timed out", request=request)
        return gateway(request)

    code, output = run_suite(handler, "--cleanup", "--no-batch")

    assert code == 1
    assert re.search(r"FAIL cleanup: organization live-smoke-org-\w+: ReadTimeout", output)
    assert "cleanup: test user" in output
    assert "cleanup: tier" in output
    assert "Result: FAILED" in output


def test_network_error_during_a_batch_cancels_it_and_runs_the_next_batch() -> None:
    gateway = FakeGateway()
    timeouts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.startswith("/v1/batches/") and not timeouts:
            timeouts.append(path)
            raise httpx.ReadTimeout("timed out", request=request)
        return gateway(request)

    code, output = run_suite(handler, "--model", "chat-a,embed-a")

    assert code == 1
    assert re.search(r"FAIL batch: chat batch on chat-a .*ReadTimeout.*; cancelled", output)
    assert "PASS batch: embedding batch on embed-a" in output
    assert len(gateway.cancelled) == 1


def test_response_that_is_not_json_fails_setup_and_still_prints_the_summary() -> None:
    gateway = FakeGateway()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/ui/api/teams/"):
            return httpx.Response(200, text="<html>maintenance</html>")
        return gateway(request)

    code, output = run_suite(handler, "--no-batch")

    assert code == 1
    assert "FAIL setup: team: unexpected response (JSONDecodeError" in output
    assert "Result: FAILED" in output


def test_master_key_in_an_error_is_redacted(tmp_path) -> None:
    gateway = FakeGateway()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/liveliness":
            return httpx.Response(500, json={"detail": f"bad header {MASTER_KEY}"})
        return gateway(request)

    report = tmp_path / "report.json"
    code, output = run_suite(handler, "--json-output", str(report))

    assert code == 1
    assert "[redacted]" in output
    assert MASTER_KEY not in output
    assert MASTER_KEY not in report.read_text()


@pytest.mark.parametrize("value", ["sk-master\nsecond-line", "sk master", "sk-master\x00"])
def test_master_key_with_control_characters_or_spaces_exits_2(value: str, capsys) -> None:
    out = io.StringIO()

    code = main(["--base-url", BASE_URL], env={"DELTALLM_MASTER_KEY": value}, out=out)

    assert code == 2
    assert value not in capsys.readouterr().err


def test_creator_model_not_shared_with_the_test_organization_is_skipped() -> None:
    gateway = FakeGateway()
    gateway.catalog += [
        {"callable_key": "other-org-model", "target_type": "model", "mode": "chat"},
        {"callable_key": "public-creator-model", "target_type": "model", "mode": "chat"},
    ]
    gateway.creator_scoped = {"other-org-model"}
    gateway.creator_public = {"public-creator-model"}

    code, output = run_suite(gateway, "--no-batch")

    assert code == 0, output
    assert "1 created by an account and not shared with the test organization" in output
    assert "SKIP  other-org-model" in output
    assert "PASS  public-creator-model" in output
