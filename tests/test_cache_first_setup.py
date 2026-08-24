from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.xiaomi_miot import (
    CONF_XIAOMI_CLOUD,
    DOMAIN,
    async_setup_xiaomi_cloud,
)
from custom_components.xiaomi_miot.core.xiaomi_cloud import MiCloudNeedVerify


TOKEN = '00' * 16
URN = 'urn:miot-spec-v2:device:fan:0000A005:dmaker-p33:1'
SCOPED_MODELS = [
    'xiaomi.vacuum.c102gl',
    'zhimi.heater.mc2a',
    'zhimi.heater.mc2a',
    'xiaomi.airp.cpa4',
    'dmaker.fan.p33',
    'dmaker.fan.p33',
    'deerma.humidifier.jsq2w',
]


def cached_device(model='dmaker.fan.p33', did='local-device', mac='00:11:22:33:44:55'):
    return {
        'did': did,
        'mac': mac,
        'name': 'Test local device',
        'model': model,
        'localip': '192.0.2.10',
        'token': TOKEN,
        'spec_type': URN,
    }


def cloud_scale():
    return {
        'did': 'cloud-scale',
        'mac': '00:11:22:33:44:66',
        'name': 'Cloud scale',
        'model': 'yunmai.scales.ms104',
        'spec_type': 'urn:miot-spec-v2:device:scale:0000A0C4:yunmai-ms104:1',
    }


class FakeDevice:
    def __init__(self, data):
        self.info = SimpleNamespace(
            host=data.get('localip', ''),
            token=data.get('token', ''),
            model=data['model'],
            did=data['did'],
            miio_info={},
            home_name='',
            room_name='',
            urn=data.get('spec_type', ''),
        )
        self.name = data['name']
        self.name_model = f"{self.name}({self.info.model})"
        self.unique_id = data['mac'].lower()
        self.conn_mode = 'auto'
        self.spec = object()

    async def get_urn(self):
        return self.info.urn


class FakeEntry:
    def __init__(self, cloud, cached, discovered):
        self.cloud = cloud
        self.cached = cached
        self.discovered = discovered
        self.cloud_ready = False
        self.created = []

    def get_config(self):
        return {
            'username': 'test-account',
            'conn_mode': 'auto',
            'config_version': 0,
        }

    async def get_cloud(self, **kwargs):
        return self.cloud

    async def get_cached_cloud_devices(self):
        return self.cached

    async def get_cloud_devices(self):
        return self.discovered

    async def new_device(self, data):
        device = FakeDevice(data)
        self.created.append((data, device))
        return device


@pytest.fixture
def setup_env(monkeypatch):
    hass = SimpleNamespace(data={DOMAIN: {'accounts': {}}})
    config_entry = SimpleNamespace(entry_id='test-entry')

    async def run(auth_result=True, auth_error=None, discovered=None):
        cloud = SimpleNamespace(
            user_id='test-user',
            async_check_auth=AsyncMock(
                return_value=auth_result,
                side_effect=auth_error,
            ),
        )
        local = cached_device()
        entry = FakeEntry(
            cloud,
            {local['did']: local},
            discovered if discovered is not None else {local['did']: local},
        )
        monkeypatch.setattr(
            'custom_components.xiaomi_miot.HassEntry.init',
            lambda _hass, _entry: entry,
        )
        monkeypatch.setattr(
            'custom_components.xiaomi_miot.MiotSpec.async_cached_type_available',
            AsyncMock(return_value=True),
        )
        result = await async_setup_xiaomi_cloud(hass, config_entry)
        return result, entry, hass

    return run


@pytest.mark.asyncio
async def test_valid_cloud_behavior_adds_cloud_only_device(setup_env):
    local = cached_device()
    scale = cloud_scale()
    result, entry, hass = await setup_env(
        discovered={local['did']: local, scale['did']: scale}
    )

    assert result is True
    assert entry.cloud_ready is True
    assert len(hass.data[DOMAIN]['test-entry']['configs']) == 2
    assert hass.data[DOMAIN]['test-entry'][CONF_XIAOMI_CLOUD] is entry.cloud


@pytest.mark.asyncio
async def test_need_verify_keeps_cached_local_device(setup_env):
    result, entry, hass = await setup_env(
        auth_error=MiCloudNeedVerify('need_verify')
    )

    assert result is True
    assert entry.cloud_ready is False
    configs = hass.data[DOMAIN]['test-entry']['configs']
    assert [cfg['model'] for cfg in configs] == ['dmaker.fan.p33']
    assert configs[0]['miot_local'] is True
    assert configs[0]['miot_cloud'] is False


@pytest.mark.asyncio
async def test_network_failure_keeps_cached_local_device(setup_env):
    result, entry, hass = await setup_env(auth_error=OSError('network down'))

    assert result is True
    assert entry.cloud_ready is False
    assert len(hass.data[DOMAIN]['test-entry']['configs']) == 1


@pytest.mark.asyncio
async def test_account_object_failure_does_not_block_cached_local(monkeypatch):
    local = cached_device()
    entry = FakeEntry(None, {local['did']: local}, {})
    entry.get_cloud = AsyncMock(side_effect=ValueError('invalid auth cache'))
    hass = SimpleNamespace(data={DOMAIN: {'accounts': {}}})
    config_entry = SimpleNamespace(entry_id='test-entry')
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.HassEntry.init',
        lambda _hass, _entry: entry,
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.MiotSpec.async_cached_type_available',
        AsyncMock(return_value=True),
    )

    assert await async_setup_xiaomi_cloud(hass, config_entry) is True
    assert len(hass.data[DOMAIN]['test-entry']['configs']) == 1
    assert hass.data[DOMAIN]['test-entry']['configs'][0]['miot_local'] is True


@pytest.mark.asyncio
async def test_false_auth_result_keeps_cached_local_device(setup_env):
    result, entry, hass = await setup_env(auth_result=False)

    assert result is True
    assert entry.cloud_ready is False
    assert len(hass.data[DOMAIN]['test-entry']['configs']) == 1


@pytest.mark.asyncio
async def test_restart_with_all_scoped_cached_devices_and_no_cloud(monkeypatch):
    cached = {}
    for index, model in enumerate(SCOPED_MODELS):
        device = cached_device(
            model=model,
            did=f'local-{index}',
            mac=f'00:11:22:33:44:{index:02x}',
        )
        cached[device['did']] = device
    scale = cloud_scale()
    cached[scale['did']] = scale
    cloud = SimpleNamespace(
        user_id='test-user',
        async_check_auth=AsyncMock(side_effect=OSError('network down')),
    )
    entry = FakeEntry(cloud, cached, cached)
    hass = SimpleNamespace(data={DOMAIN: {'accounts': {}}})
    config_entry = SimpleNamespace(entry_id='test-entry')
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.HassEntry.init',
        lambda _hass, _entry: entry,
    )
    spec_cache = AsyncMock(return_value=True)
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.MiotSpec.async_cached_type_available',
        spec_cache,
    )

    assert await async_setup_xiaomi_cloud(hass, config_entry) is True
    configs = hass.data[DOMAIN]['test-entry']['configs']
    assert [config['model'] for config in configs] == SCOPED_MODELS
    assert all(config['miot_local'] for config in configs)
    assert all(not config['miot_cloud'] for config in configs)
    assert 'yunmai.scales.ms104' not in [config['model'] for config in configs]
    assert len(entry.created) == 7
