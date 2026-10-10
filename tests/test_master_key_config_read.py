"""Master-key checks read one committed scalar, not a full config copy."""

from fastapi import FastAPI
import pytest
from starlette.requests import Request

from src.config import Settings
from src.config_runtime.dynamic import DynamicConfigManager
from src.middleware.auth import _is_master_key

pytestmark = pytest.mark.hermetic
KEY = "sk-unit-master-key-0123456789abcdefA1"
NEXT_KEY = "sk-next-master-key-0123456789abcdefB2"


def request_for(manager):
    app = FastAPI()
    app.state.dynamic_config_manager = manager
    app.state.settings = Settings(master_key=KEY)
    return Request({"type": "http", "app": app, "path": "/v1/chat/completions"})


def manager_for(key=KEY):
    return DynamicConfigManager(None, None, {"general_settings": {"master_key": key}})


def test_authentication_does_not_copy_the_application_configuration(monkeypatch):
    manager = manager_for()

    def forbidden_copy():
        raise AssertionError("request copied the complete configuration")

    monkeypatch.setattr(manager, "get_app_config", forbidden_copy)
    request = request_for(manager)
    assert _is_master_key(request, KEY)
    assert not _is_master_key(request, NEXT_KEY)
    assert not _is_master_key(request, "")


async def test_scalar_read_follows_committed_generation_and_copy_isolation():
    manager = manager_for()
    request = request_for(manager)
    caller_copy = manager.get_app_config()
    caller_copy.general_settings.master_key = NEXT_KEY
    assert manager.get_master_key() == KEY
    assert _is_master_key(request, KEY)
    await manager._apply_db_config({"general_settings": {"master_key": NEXT_KEY}})
    assert manager.get_master_key() == NEXT_KEY
    assert _is_master_key(request, NEXT_KEY)
    assert not _is_master_key(request, KEY)


def test_missing_dynamic_key_does_not_authorize_the_startup_fallback():
    assert not _is_master_key(request_for(manager_for(None)), KEY)


async def test_failed_reload_keeps_the_committed_key_and_generation():
    manager = manager_for()
    request = request_for(manager)
    generation = manager.get_config_generation()

    def reject_candidate(config, changes):
        assert manager.get_master_key() == KEY
        if config.general_settings.master_key == NEXT_KEY:
            raise RuntimeError("subscriber rejected the candidate")

    manager.subscribe(reject_candidate)
    with pytest.raises(RuntimeError, match="rejected the candidate"):
        await manager._apply_db_config({"general_settings": {"master_key": NEXT_KEY}})
    assert manager.get_config_generation() == generation
    assert manager.get_master_key() == KEY
    assert _is_master_key(request, KEY)
    assert not _is_master_key(request, NEXT_KEY)


def test_startup_key_fallback_is_used_only_without_a_dynamic_manager():
    request = request_for(None)
    assert _is_master_key(request, KEY)
    assert not _is_master_key(request, NEXT_KEY)
