import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import platform

import pytest

from custom_components.xiaomi_miot.core.miot_spec import MiotSpec
from custom_components.xiaomi_miot.core.hass_entry import HassEntry
from custom_components.xiaomi_miot.core.xiaomi_cloud import MiotCloud
from custom_components.xiaomi_miot import async_cloud_bootstrap_with_retry


URN = 'urn:miot-spec-v2:device:fan:0000A005:dmaker-p33:1'


class FakeStore:
    values = {}

    def __init__(self, _hass, _version, key):
        self.key = key

    async def async_load(self):
        return self.values.get(self.key)

    async def async_save(self, value):
        self.values[self.key] = value

    async def async_remove(self):
        self.values.pop(self.key, None)


@pytest.mark.asyncio
async def test_stale_device_cache_ignores_discovery_ttl(monkeypatch):
    FakeStore.values = {
        'xiaomi_miot/devices-test-user-sg.json': {
            'update_time': 1,
            'devices': [{'did': 'cached-device'}],
        }
    }
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.xiaomi_cloud.Store', FakeStore
    )
    cloud = object.__new__(MiotCloud)
    cloud.user_id = 'test-user'
    cloud.default_server = 'sg'
    cloud.hass = SimpleNamespace()

    assert await cloud.async_get_cached_devices() == [{'did': 'cached-device'}]


def test_cached_device_index_does_not_require_cloud_instance():
    device = {
        'did': 'cached-device',
        'mac': '00:11:22:33:44:55',
        'model': 'dmaker.fan.p33',
    }

    assert MiotCloud.devices_by_key([device], 'did') == {
        'cached-device': device
    }


@pytest.mark.asyncio
async def test_hass_entry_reads_discovery_cache_without_account_session(monkeypatch):
    config_entry = SimpleNamespace(
        entry_id='test-entry',
        data={
            'username': 'account',
            'user_id': 'test-user',
            'server_country': 'sg',
        },
        options={},
    )
    entry = HassEntry(SimpleNamespace(), config_entry)
    cached = [{
        'did': 'cached-device',
        'mac': '00:11:22:33:44:55',
        'model': 'dmaker.fan.p33',
    }]
    loader = AsyncMock(return_value=cached)
    monkeypatch.setattr(MiotCloud, 'async_load_cached_devices', loader)
    entry.get_cloud = AsyncMock(side_effect=AssertionError('account constructed'))

    assert await entry.get_cached_cloud_devices() == {
        'cached-device': cached[0]
    }
    loader.assert_awaited_once_with(entry.hass, 'test-user', 'sg')
    entry.get_cloud.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_cached_spec_never_calls_endpoint(monkeypatch):
    spec_key = f'xiaomi_miot/{URN}.json'
    lang_key = f'xiaomi_miot/spec-langs/{URN}.json'
    if platform.system() == 'Windows':
        spec_key = spec_key.replace(':', '_')
        lang_key = lang_key.replace(':', '_')
    FakeStore.values = {
        spec_key: {
            '_updated_time': 1,
            'type': URN,
            'services': [
                {
                    'iid': 2,
                    'type': 'urn:miot-spec-v2:service:fan:00007808:1',
                    'description': 'Fan',
                    'properties': [],
                    'actions': [],
                }
            ],
        },
        lang_key: {
            '_updated_time': 1,
            'type': URN,
            'data': {'en': {}},
        },
    }
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.Store', FakeStore
    )
    download = AsyncMock(side_effect=AssertionError('network fetch attempted'))
    monkeypatch.setattr(MiotSpec, 'async_download_miot_spec', download)

    hass = SimpleNamespace(config=SimpleNamespace(language='en'))
    spec = await MiotSpec.async_from_type(hass, URN, cache_only=True)

    assert spec.type == URN
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_cached_spec_does_not_call_endpoint(monkeypatch):
    FakeStore.values = {}
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.Store', FakeStore
    )
    download = AsyncMock(side_effect=AssertionError('network fetch attempted'))
    monkeypatch.setattr(MiotSpec, 'async_download_miot_spec', download)

    assert await MiotSpec.async_from_type(
        SimpleNamespace(), URN, cache_only=True
    ) is None
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_explicit_spec_refresh_preserves_known_good_cache(monkeypatch):
    spec_key = f'xiaomi_miot/{URN}.json'
    lang_key = f'xiaomi_miot/spec-langs/{URN}.json'
    if platform.system() == 'Windows':
        spec_key = spec_key.replace(':', '_')
        lang_key = lang_key.replace(':', '_')
    FakeStore.values = {
        spec_key: {
            '_updated_time': 1,
            'type': URN,
            'services': [{
                'iid': 2,
                'type': 'urn:miot-spec-v2:service:fan:00007808:1',
                'description': 'Fan',
                'properties': [],
                'actions': [],
            }],
        },
        lang_key: {
            '_updated_time': 1,
            'type': URN,
            'data': {'en': {}},
        },
    }
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.Store', FakeStore
    )
    download = AsyncMock(side_effect=[
        {'type': URN, 'services': []},
        {'data': {}},
    ])
    monkeypatch.setattr(MiotSpec, 'async_download_miot_spec', download)

    spec = await MiotSpec.async_from_type(
        SimpleNamespace(config=SimpleNamespace(language='en')),
        URN,
        use_remote=True,
    )

    assert spec.type == URN
    assert spec.services
    assert FakeStore.values[spec_key]['services']
    assert FakeStore.values[lang_key]['type'] == URN


@pytest.mark.asyncio
async def test_invalid_model_index_refresh_preserves_known_good_cache(monkeypatch):
    FakeStore.values = {
        'xiaomi_miot/instances.json': {
            '_updated_time': 1,
            'dmaker.fan.p33': {
                'type': URN,
                'status': 'released',
                'version': 1,
            },
        },
    }
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.Store', FakeStore
    )
    monkeypatch.setattr(
        MiotSpec,
        'async_download_miot_spec',
        AsyncMock(return_value={'error': 'partial response'}),
    )

    spec_type = await MiotSpec.async_get_model_type(
        SimpleNamespace(),
        'dmaker.fan.p33',
        use_remote=True,
    )

    assert spec_type == URN
    assert (
        FakeStore.values['xiaomi_miot/instances.json']['dmaker.fan.p33']['type']
        == URN
    )


@pytest.mark.asyncio
async def test_hanging_language_fetch_propagates_cancellation(monkeypatch):
    FakeStore.values = {}
    started = asyncio.Event()

    async def hanging_download(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.Store', FakeStore
    )
    monkeypatch.setattr(MiotSpec, 'async_download_miot_spec', hanging_download)

    task = asyncio.create_task(MiotSpec.async_get_langs(SimpleNamespace(), URN))
    await asyncio.wait_for(started.wait(), 0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.1)


@pytest.mark.asyncio
async def test_miot_download_propagates_cancelled_request(monkeypatch):
    class CancelledSession:
        async def get(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.async_get_clientsession',
        lambda _hass: CancelledSession(),
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            MiotSpec.async_download_miot_spec(
                SimpleNamespace(),
                '/instance/v2/multiLanguage?urn=test',
            ),
            0.1,
        )


@pytest.mark.asyncio
async def test_cloud_bootstrap_timeout_bounds_hanging_language_fetch(monkeypatch):
    FakeStore.values = {}
    bootstrap_calls = 0
    second_attempt = asyncio.Event()

    async def hanging_download(*_args, **_kwargs):
        await asyncio.Future()

    async def bootstrap():
        nonlocal bootstrap_calls
        bootstrap_calls += 1
        if bootstrap_calls == 2:
            second_attempt.set()
        await MiotSpec.async_get_langs(SimpleNamespace(), URN)

    entry = SimpleNamespace(cloud_ready=False, cloud_devices={'stale': object()})
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.core.miot_spec.Store', FakeStore
    )
    monkeypatch.setattr(MiotSpec, 'async_download_miot_spec', hanging_download)
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_TIMEOUT', 0.01
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_RETRY_DELAYS', (0,)
    )

    task = asyncio.create_task(
        async_cloud_bootstrap_with_retry(entry, 'test-account', 1, bootstrap)
    )
    try:
        await asyncio.wait_for(second_attempt.wait(), 0.1)
        assert bootstrap_calls == 2
        assert entry.cloud_ready is False
    finally:
        task.cancel()
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            0.1,
        )
