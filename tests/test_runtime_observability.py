import asyncio
import json
from types import SimpleNamespace

import pytest

from custom_components.xiaomi_miot import (
    DOMAIN,
    async_cloud_bootstrap_with_retry,
    async_retry_cloud_service,
)
from custom_components.xiaomi_miot.core.hass_entry import HassEntry
from custom_components.xiaomi_miot.core.runtime_status import (
    CloudBootstrapStatus,
    runtime_snapshot,
)
from custom_components.xiaomi_miot.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.xiaomi_miot.system_health import system_health_info


def device(*, local=True, local_state=True, available=True, spec=True, model="test"):
    return SimpleNamespace(
        name=model,
        model=model,
        use_local=local,
        _local_state=local_state,
        available=available,
        spec=object() if spec else None,
    )


def entry_with_devices(devices, *, cloud_ready=False, cached_cloud_only=0):
    return SimpleNamespace(
        devices={str(index): item for index, item in enumerate(devices)},
        cloud_ready=cloud_ready,
        local_cache_usable=True,
        cached_cloud_only_devices=cached_cloud_only,
        cloud_bootstrap_status=CloudBootstrapStatus(),
        entry=SimpleNamespace(state=SimpleNamespace(value="loaded")),
    )


def test_runtime_status_local_and_cloud():
    entry = entry_with_devices(
        [device(), device(local=False, model="cloud", spec=True)],
        cloud_ready=True,
    )
    assert runtime_snapshot(entry)["status"] == "local_and_cloud"


def test_runtime_status_local_only_when_cloud_unavailable():
    entry = entry_with_devices([device()], cloud_ready=False, cached_cloud_only=1)
    assert runtime_snapshot(entry)["status"] == "local_only"


def test_cloud_bootstrap_state_transitions_are_deterministic():
    status = CloudBootstrapStatus()
    status.attempt_started()
    assert status.state == "running"
    assert status.attempt_count == 1

    status.failed(TimeoutError(), 60)
    assert status.state == "backoff"
    assert status.last_failure_class == "timeout"
    assert status.next_retry_at is not None

    status.attempt_started()
    status.auth_succeeded()
    status.discovery_succeeded()
    status.succeeded()
    assert status.state == "ready"
    assert status.retry_level == 0
    assert status.last_auth_success_at is not None
    assert status.last_discovery_success_at is not None
    assert status.last_success_at is not None


def test_cloud_failure_state_does_not_retain_exception_message():
    status = CloudBootstrapStatus()
    status.attempt_started()
    status.failed(OSError("token=must-not-survive"), 60)

    rendered = json.dumps(status.as_dict())
    assert status.last_failure_class == "network_unreachable"
    assert "must-not-survive" not in rendered


def test_glafira_partial_failure_is_degraded():
    entry = entry_with_devices(
        [device(), device(local=False, model="yunmai.scales.ms104", spec=False)],
        cloud_ready=True,
    )
    snapshot = runtime_snapshot(entry)
    assert snapshot["status"] == "degraded"
    assert snapshot["cloud_only_devices"] == 1
    assert snapshot["cloud_only_available_devices"] == 0


def test_one_offline_local_device_does_not_degrade_runtime():
    entry = entry_with_devices(
        [device(local_state=True), device(local_state=False)],
        cloud_ready=True,
    )
    snapshot = runtime_snapshot(entry)
    assert snapshot["status"] == "local_and_cloud"
    assert snapshot["local_reachable_devices"] == 1
    assert snapshot["local_unavailable_devices"] == 1


class FakeConfigEntry:
    def __init__(self):
        self.entry_id = "runtime-entry"
        self.data = {"username": "account"}
        self.options = {}
        self.state = SimpleNamespace(value="loaded")
        self.tasks = []

    def async_create_background_task(self, _hass, coro, name, eager_start=True):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


@pytest.mark.asyncio
async def test_manual_retry_idle_and_already_running():
    config_entry = FakeConfigEntry()
    entry = HassEntry(SimpleNamespace(), config_entry)
    started = asyncio.Event()

    async def pending():
        started.set()
        await asyncio.Future()

    entry.set_cloud_bootstrap(pending)
    assert entry.request_cloud_retry() == "started"
    await asyncio.wait_for(started.wait(), 0.1)
    assert entry.request_cloud_retry() == "already_running"
    await entry.async_cancel_cloud_bootstrap()


@pytest.mark.asyncio
async def test_manual_retry_interrupts_backoff(monkeypatch):
    config_entry = FakeConfigEntry()
    entry = HassEntry(SimpleNamespace(), config_entry)
    attempts = 0

    async def bootstrap():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("not retained")
        entry.cloud_ready = True

    monkeypatch.setattr(
        "custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_RETRY_DELAYS", (3600,)
    )
    entry.set_cloud_bootstrap(
        lambda: async_cloud_bootstrap_with_retry(entry, "account", 1, bootstrap)
    )
    entry.start_cloud_bootstrap()
    for _ in range(20):
        if entry.cloud_bootstrap_status.state == "backoff":
            break
        await asyncio.sleep(0)
    assert entry.cloud_bootstrap_status.state == "backoff"
    assert entry.request_cloud_retry() == "started"
    await asyncio.wait_for(entry.cloud_bootstrap_task, 0.1)
    assert attempts == 2
    assert entry.cloud_bootstrap_status.state == "ready"
    assert entry.cloud_bootstrap_status.last_success_at is not None


@pytest.mark.asyncio
async def test_external_cloud_recovery_settles_backoff_without_duplicate_retry(
    monkeypatch,
):
    config_entry = FakeConfigEntry()
    entry = HassEntry(SimpleNamespace(), config_entry)
    attempts = 0

    async def bootstrap():
        nonlocal attempts
        attempts += 1
        raise OSError("not retained")

    monkeypatch.setattr(
        "custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_RETRY_DELAYS", (3600,)
    )
    entry.set_cloud_bootstrap(
        lambda: async_cloud_bootstrap_with_retry(entry, "account", 1, bootstrap)
    )
    entry.start_cloud_bootstrap()
    for _ in range(20):
        if entry.cloud_bootstrap_status.state == "backoff":
            break
        await asyncio.sleep(0)

    entry.cloud_ready = True
    entry.cloud_bootstrap_status.succeeded()
    entry.cloud_retry_event.set()
    await asyncio.wait_for(entry.cloud_bootstrap_task, 0.1)

    assert attempts == 1
    assert entry.cloud_bootstrap_status.state == "ready"


@pytest.mark.asyncio
async def test_retry_service_reports_safe_state():
    config_entry = FakeConfigEntry()
    entry = HassEntry(SimpleNamespace(), config_entry)
    entry.set_cloud_bootstrap(lambda: asyncio.sleep(0))
    HassEntry.ALL[entry.id] = entry
    try:
        response = await async_retry_cloud_service(
            SimpleNamespace(data={"config_entry_id": entry.id})
        )
        assert response["status"] == "started"
        assert set(response) == {"status", "cloud_ready", "bootstrap_state"}
        await entry.cloud_bootstrap_task
    finally:
        HassEntry.ALL.pop(entry.id, None)


@pytest.mark.asyncio
async def test_diagnostics_are_redacted_and_report_cache_provenance(monkeypatch):
    secret_token = "ab" * 16
    secret_service_token = "service-secret"

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            pass

        async def async_load(self):
            return {
                "devices": [{"model": "dmaker.fan.p33", "token": secret_token}],
                "refresh_metadata": {
                    "schema_version": 2,
                    "source": "cache-first-local-runtime",
                    "integration_version": "1.1.4",
                    "server": "sg",
                    "account_user_id": "sensitive-account-id",
                    "last_successful_cloud_refresh": "2026-08-25T00:00:00+00:00",
                    "service_token": secret_service_token,
                },
            }

    monkeypatch.setattr(
        "custom_components.xiaomi_miot.core.runtime_status.Store", FakeStore
    )
    config_entry = FakeConfigEntry()
    config_entry.data = {
        "username": "private@example.invalid",
        "password": "password-secret",
        "user_id": "account-id",
        "server_country": "sg",
    }
    hass = SimpleNamespace()
    entry = HassEntry(hass, config_entry)
    entry.local_cache_usable = True
    entry.cached_cloud_only_devices = 1
    entry.devices = {
        "local": device(model="dmaker.fan.p33"),
        "scale": device(local=False, model="yunmai.scales.ms104", spec=False),
    }
    HassEntry.ALL[entry.id] = entry
    try:
        diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    finally:
        HassEntry.ALL.pop(entry.id, None)

    rendered = json.dumps(diagnostics)
    for secret in (
        secret_token,
        secret_service_token,
        "password-secret",
        "private@example.invalid",
        "account-id",
        "sensitive-account-id",
    ):
        assert secret not in rendered
    lowered = rendered.lower()
    for forbidden_key in (
        "token",
        "password",
        "cookie",
        "ssecurity",
        "auth_payload",
    ):
        assert forbidden_key not in lowered
    assert diagnostics["cache"] == {
        "usable": True,
        "schema_version": 2,
        "source": "cache-first-local-runtime",
        "integration_version": "1.1.4",
        "server": "sg",
        "account_scope_present": True,
        "last_successful_refresh": "2026-08-25T00:00:00+00:00",
        "cached_devices_count": 1,
    }
    assert diagnostics["runtime"]["status"] == "local_only"


@pytest.mark.asyncio
async def test_system_health_uses_runtime_state_without_device_polling(monkeypatch):
    config_entry = FakeConfigEntry()
    entry = HassEntry(SimpleNamespace(), config_entry)
    entry.local_cache_usable = True
    entry.cloud_ready = True
    local = device(model="dmaker.fan.p33")
    entry.devices = {"local": local}
    HassEntry.ALL[entry.id] = entry
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.system_health.async_get_manifest",
        lambda *_args: asyncio.sleep(0, result="1.1.4"),
    )
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.system_health.async_cache_snapshot",
        lambda *_args: asyncio.sleep(
            0,
            result={
                "last_successful_refresh": "2026-08-25T00:00:00+00:00",
            },
        ),
    )
    try:
        result = await system_health_info(SimpleNamespace())
    finally:
        HassEntry.ALL.pop(entry.id, None)

    assert result["runtime_status"] == "local_and_cloud"
    assert result["local_reachable_devices"] == 1
    assert result["last_successful_cache_refresh"] == (
        "2026-08-25T00:00:00+00:00"
    )
    assert local._local_state is True
