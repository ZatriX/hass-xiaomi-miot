import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from custom_components.xiaomi_miot.core.cloud_refresh import (
    CloudDiscoveryRefreshError,
    CloudDiscoveryRefreshResult,
    async_refresh_cloud_discovery,
)
from custom_components.xiaomi_miot.core.hass_entry import HassEntry
from custom_components.xiaomi_miot.core.miot_spec import MiotSpec


TOKEN_A = "00" * 16
TOKEN_B = "11" * 16
LOCAL_MODEL = "dmaker.fan.p33"
CLOUD_MODEL = "yunmai.scales.ms104"
URN = "urn:miot-spec-v2:device:fan:0000A005:dmaker-p33:1"
LOCAL_MODELS = {LOCAL_MODEL}


def local_device(
    did="local-1",
    ip="192.0.2.10",
    token=TOKEN_A,
    mac="00:11:22:33:44:55",
):
    return {
        "did": did,
        "mac": mac,
        "name": "Local fan",
        "model": LOCAL_MODEL,
        "localip": ip,
        "token": token,
        "spec_type": URN,
    }


def cloud_device(did="cloud-1"):
    return {
        "did": did,
        "mac": "00:11:22:33:44:66",
        "name": "Cloud scale",
        "model": CLOUD_MODEL,
    }


class FakeCloud:
    def __init__(self, cached=None, fresh=None, auth=True):
        self.payload = {
            "update_time": 1,
            "devices": copy.deepcopy(cached or []),
            "homes": [{"id": "old-home"}],
        }
        self.fresh = {
            "devices": copy.deepcopy(fresh or []),
            "homes": [{"id": "fresh-home"}],
        }
        self.auth = auth
        self.default_server = "sg"
        self.user_id = "test-user"
        self.saved = []
        self.discovery_error = None
        self.save_error = None

    async def async_check_auth(self, notify=False):
        assert notify is False
        if isinstance(self.auth, Exception):
            raise self.auth
        return self.auth

    async def async_load_device_cache_payload(self):
        return copy.deepcopy(self.payload)

    async def async_discover_devices(self):
        if self.discovery_error:
            raise self.discovery_error
        return copy.deepcopy(self.fresh)

    async def async_save_device_cache_payload(self, payload):
        if self.save_error:
            raise self.save_error
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


@pytest.fixture(autouse=True)
def spec_cache(monkeypatch):
    monkeypatch.setattr(
        MiotSpec,
        "async_get_model_type",
        AsyncMock(return_value=URN),
    )
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.core.cloud_refresh._refresh_model_spec",
        AsyncMock(return_value=(URN, True)),
    )


@pytest.mark.asyncio
async def test_existing_cache_merges_new_local_device_and_preserves_identity():
    existing = local_device()
    new = local_device(
        did="local-2",
        ip="192.0.2.11",
        token=TOKEN_B,
        mac="00:11:22:33:44:77",
    )
    cloud = FakeCloud(cached=[existing], fresh=[existing, new])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.discovered == 2
    assert result.new == 1
    assert result.local_capable_new == 1
    assert result.cloud_only_new == 0
    assert result.failed == 0
    assert len(cloud.payload["devices"]) == 2
    assert cloud.payload["refresh_metadata"]["schema_version"] == 2
    assert cloud.payload["refresh_metadata"]["server"] == "sg"
    old_after = next(d for d in cloud.payload["devices"] if d["did"] == "local-1")
    assert old_after["did"] == existing["did"]
    assert old_after["mac"] == existing["mac"]
    assert TOKEN_A not in str(result.as_dict())
    assert TOKEN_B not in str(result.as_dict())


@pytest.mark.asyncio
async def test_new_cloud_only_device_is_persisted_without_local_credentials():
    cloud = FakeCloud(cached=[local_device()], fresh=[cloud_device()])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.new == 1
    assert result.local_capable_new == 0
    assert result.cloud_only_new == 1
    scale = next(d for d in cloud.payload["devices"] if d["did"] == "cloud-1")
    assert scale["model"] == CLOUD_MODEL
    assert "token" not in scale


@pytest.mark.asyncio
async def test_device_absent_from_refresh_is_not_deleted():
    existing = local_device()
    cloud = FakeCloud(cached=[existing], fresh=[])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.discovered == 0
    assert result.unchanged == 1
    assert cloud.payload["devices"] == [existing]


@pytest.mark.asyncio
async def test_changed_ip_and_token_update_only_after_validation():
    existing = local_device()
    changed = local_device(ip="192.0.2.99", token=TOKEN_B)
    cloud = FakeCloud(cached=[existing], fresh=[changed])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.updated == 1
    assert result.failed == 0
    assert cloud.payload["devices"][0]["localip"] == "192.0.2.99"
    assert cloud.payload["devices"][0]["token"] == TOKEN_B


@pytest.mark.asyncio
async def test_existing_mac_identity_is_never_replaced_by_discovery():
    existing = local_device()
    fresh = local_device(mac="aa:bb:cc:dd:ee:ff")
    cloud = FakeCloud(cached=[existing], fresh=[fresh])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.updated == 0
    assert result.unchanged == 1
    assert cloud.payload["devices"][0]["mac"] == existing["mac"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate",
    [
        local_device(did="invalid", ip="not-an-ip"),
        local_device(did="invalid", token="not-a-token"),
    ],
)
async def test_invalid_new_local_candidate_is_skipped(candidate):
    existing = local_device()
    cloud = FakeCloud(cached=[existing], fresh=[candidate])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.failed == 1
    assert result.new == 0
    assert cloud.payload["devices"] == [existing]


@pytest.mark.asyncio
async def test_missing_new_local_spec_is_skipped(monkeypatch):
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.core.cloud_refresh._refresh_model_spec",
        AsyncMock(return_value=(URN, False)),
    )
    existing = local_device()
    cloud = FakeCloud(cached=[existing], fresh=[local_device(did="invalid")])

    result = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert result.failed == 1
    assert cloud.payload["devices"] == [existing]


@pytest.mark.asyncio
async def test_partial_discovery_failure_does_not_commit():
    existing = local_device()
    cloud = FakeCloud(cached=[existing])
    cloud.discovery_error = OSError("network down")

    with pytest.raises(CloudDiscoveryRefreshError, match="existing cache"):
        await async_refresh_cloud_discovery(SimpleNamespace(), cloud, LOCAL_MODELS)

    assert cloud.payload["devices"] == [existing]
    assert cloud.saved == []


@pytest.mark.asyncio
async def test_expired_auth_does_not_discover_or_commit():
    existing = local_device()
    cloud = FakeCloud(cached=[existing], fresh=[local_device(did="new")], auth=False)
    cloud.async_discover_devices = AsyncMock(side_effect=AssertionError("discovery called"))

    with pytest.raises(CloudDiscoveryRefreshError, match="authentication"):
        await async_refresh_cloud_discovery(SimpleNamespace(), cloud, LOCAL_MODELS)

    cloud.async_discover_devices.assert_not_awaited()
    assert cloud.payload["devices"] == [existing]
    assert cloud.saved == []


@pytest.mark.asyncio
async def test_atomic_store_failure_leaves_in_memory_known_good_cache():
    existing = local_device()
    cloud = FakeCloud(cached=[existing], fresh=[local_device(did="new")])
    cloud.save_error = OSError("disk full")

    with pytest.raises(CloudDiscoveryRefreshError, match="commit failed"):
        await async_refresh_cloud_discovery(SimpleNamespace(), cloud, LOCAL_MODELS)

    assert cloud.payload["devices"] == [existing]
    assert cloud.saved == []


@pytest.mark.asyncio
async def test_second_refresh_is_idempotent_and_has_no_duplicates():
    existing = local_device()
    new = local_device(did="new", mac="00:11:22:33:44:77")
    cloud = FakeCloud(cached=[existing], fresh=[existing, new])

    first = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )
    second = await async_refresh_cloud_discovery(
        SimpleNamespace(), cloud, LOCAL_MODELS
    )

    assert first.new == 1
    assert second.new == 0
    assert second.updated == 0
    assert second.unchanged == 2
    assert [d["did"] for d in cloud.payload["devices"]] == ["local-1", "new"]
    assert len({d["did"] for d in cloud.payload["devices"]}) == 2


class FakeEntry:
    def __init__(self):
        self.id = "entry-1"
        self.cloud_devices = {"old": object()}
        self.cloud = object()
        self.cloud_ready = False
        self.adders = {}

    def get_config(self, key=None):
        data = {"user_id": "test-user", "username": "account"}
        return data.get(key) if key else data

    async def get_cloud(self, login=False):
        assert login is False
        return self.cloud


@pytest.mark.asyncio
async def test_service_auth_failure_never_reloads_or_clears_runtime(monkeypatch):
    from custom_components.xiaomi_miot import async_refresh_devices_service

    entry = FakeEntry()
    monkeypatch.setattr(HassEntry, "ALL", {entry.id: entry})
    reload_entry = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_entry))
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.async_refresh_cloud_discovery",
        AsyncMock(side_effect=CloudDiscoveryRefreshError("authentication failed")),
    )

    with pytest.raises(CloudDiscoveryRefreshError, match="authentication"):
        await async_refresh_devices_service(
            SimpleNamespace(
                hass=hass,
                data={"config_entry_id": entry.id},
            )
        )

    reload_entry.assert_not_awaited()
    assert list(entry.cloud_devices) == ["old"]


@pytest.mark.asyncio
async def test_service_noop_refresh_does_not_interrupt_runtime(monkeypatch):
    from custom_components.xiaomi_miot import async_refresh_devices_service

    entry = FakeEntry()
    monkeypatch.setattr(HassEntry, "ALL", {entry.id: entry})
    reload_entry = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_entry))
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.async_refresh_cloud_discovery",
        AsyncMock(return_value=CloudDiscoveryRefreshResult(unchanged=1)),
    )

    result = await async_refresh_devices_service(
        SimpleNamespace(
            hass=hass,
            data={"config_entry_id": entry.id},
        )
    )

    assert result["unchanged"] == 1
    assert result["reloaded"] is False
    reload_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_noop_refresh_retriggers_cloud_coordinators(monkeypatch):
    from custom_components.xiaomi_miot import async_refresh_devices_service

    entry = FakeEntry()
    entry.adders['sensor'] = object()
    monkeypatch.setattr(HassEntry, "ALL", {entry.id: entry})
    reload_entry = AsyncMock()
    recover = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_entry))
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.async_refresh_cloud_discovery",
        AsyncMock(return_value=CloudDiscoveryRefreshResult(unchanged=1)),
    )
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.sensor.async_setup_cloud_entities",
        recover,
    )

    result = await async_refresh_devices_service(
        SimpleNamespace(
            hass=hass,
            data={"config_entry_id": entry.id},
        )
    )

    assert result["unchanged"] == 1
    assert result["reloaded"] is False
    assert entry.cloud_ready is True
    recover.assert_awaited_once_with(hass, entry)
    reload_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_reloads_once_after_successful_change(monkeypatch):
    from custom_components.xiaomi_miot import async_refresh_devices_service

    entry = FakeEntry()
    monkeypatch.setattr(HassEntry, "ALL", {entry.id: entry})
    reload_entry = AsyncMock(return_value=True)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_entry))
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.async_refresh_cloud_discovery",
        AsyncMock(return_value=CloudDiscoveryRefreshResult(new=1)),
    )

    result = await async_refresh_devices_service(
        SimpleNamespace(
            hass=hass,
            data={"config_entry_id": entry.id},
        )
    )

    assert result["new"] == 1
    assert result["reloaded"] is True
    reload_entry.assert_awaited_once_with(entry.id)
    assert entry.cloud_devices is None


@pytest.mark.asyncio
async def test_registered_refresh_service_invokes_handler_with_ha_contract(
    monkeypatch,
    tmp_path,
):
    from custom_components.xiaomi_miot import (
        DOMAIN,
        async_setup_component_services,
    )

    hass = HomeAssistant(str(tmp_path))
    entry = FakeEntry()
    owner_id = "owner-1"
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(
            return_value=SimpleNamespace(id=owner_id, is_admin=True)
        )
    )
    monkeypatch.setattr(HassEntry, "ALL", {entry.id: entry})
    refresh = AsyncMock(
        return_value=CloudDiscoveryRefreshResult(
            discovered=8,
            unchanged=8,
            failed=1,
        )
    )
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.async_refresh_cloud_discovery",
        refresh,
    )
    await async_setup_component_services(hass)

    try:
        assert (
            hass.services.supports_response(DOMAIN, "renew_devices")
            is SupportsResponse.OPTIONAL
        )
        result = await hass.services.async_call(
            DOMAIN,
            "renew_devices",
            {"config_entry_id": entry.id},
            blocking=True,
            context=Context(user_id=owner_id),
            return_response=True,
        )
    finally:
        await hass.async_stop(force=True)

    assert result == {
        "discovered": 8,
        "new": 0,
        "updated": 0,
        "unchanged": 8,
        "local_capable_new": 0,
        "cloud_only_new": 0,
        "failed": 1,
        "new_models": [],
        "reloaded": False,
    }
    refresh.assert_awaited_once()
    refresh_hass, refresh_cloud, refresh_local_models = refresh.await_args.args
    assert refresh_hass is hass
    assert refresh_cloud is entry.cloud
    assert LOCAL_MODEL in refresh_local_models


@pytest.mark.asyncio
async def test_registered_refresh_service_redacts_unexpected_failure(
    caplog,
    monkeypatch,
    tmp_path,
):
    from custom_components.xiaomi_miot import (
        DOMAIN,
        async_setup_component_services,
    )

    hass = HomeAssistant(str(tmp_path))
    entry = FakeEntry()
    secret = "token=0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(HassEntry, "ALL", {entry.id: entry})
    monkeypatch.setattr(
        "custom_components.xiaomi_miot.async_refresh_cloud_discovery",
        AsyncMock(side_effect=RuntimeError(secret)),
    )
    await async_setup_component_services(hass)

    try:
        with pytest.raises(
            HomeAssistantError,
            match="existing cache remains active",
        ) as err:
            await hass.services.async_call(
                DOMAIN,
                "renew_devices",
                {"config_entry_id": entry.id},
                blocking=True,
                return_response=True,
            )
    finally:
        await hass.async_stop(force=True)

    assert secret not in str(err.value)
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text
    assert list(entry.cloud_devices) == ["old"]
