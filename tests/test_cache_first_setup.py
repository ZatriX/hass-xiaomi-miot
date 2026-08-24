import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.xiaomi_miot import (
    CONF_XIAOMI_CLOUD,
    DOMAIN,
    async_cloud_bootstrap_with_retry,
    async_setup_entry,
    async_setup_xiaomi_cloud,
)
from custom_components.xiaomi_miot.core.hass_entry import HassEntry
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
        self.added_domains = []

    async def get_urn(self):
        return self.info.urn

    def add_entities(self, domain):
        self.added_domains.append(domain)


class FakeEntry:
    def __init__(self, cloud, cached, discovered):
        self.cloud = cloud
        self.cached = cached
        self.discovered = discovered
        self.cloud_ready = False
        self.created = []
        self.adders = {}
        self.cloud_devices = None
        self.cloud_bootstrap_factory = None
        self.cloud_bootstrap_task = None

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

    def set_cloud_bootstrap(self, factory):
        self.cloud_bootstrap_factory = factory

    def start_cloud_bootstrap(self):
        if not self.cloud_bootstrap_factory:
            return None
        self.cloud_bootstrap_task = asyncio.create_task(self.cloud_bootstrap_factory())
        return self.cloud_bootstrap_task


class FakeConfigEntry:
    def __init__(self):
        self.entry_id = 'test-entry'
        self.data = {'username': 'test-account'}
        self.options = {}
        self.update_listeners = []
        self.background_tasks = []

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)

    def async_create_background_task(self, hass, coro, name, eager_start=True):
        task = asyncio.create_task(coro, name=name)
        self.background_tasks.append(task)
        return task

    async def cancel_background_tasks(self):
        for task in self.background_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)


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
async def test_real_blackhole_does_not_hold_config_entry_setup(monkeypatch):
    """A pending cloud socket timeout must not hold local platform setup open."""
    cloud_started = asyncio.Event()

    async def blackholed_auth(*_args, **_kwargs):
        cloud_started.set()
        await asyncio.Future()

    local = cached_device()
    cloud = SimpleNamespace(
        user_id='test-user',
        async_check_auth=blackholed_auth,
    )
    entry = FakeEntry(cloud, {local['did']: local}, {local['did']: local})
    config_entry = FakeConfigEntry()
    forward_setups = AsyncMock()
    hass = SimpleNamespace(
        data={DOMAIN: {'accounts': {}}},
        config_entries=SimpleNamespace(async_forward_entry_setups=forward_setups),
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.HassEntry.init',
        lambda _hass, _entry: entry,
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.MiotSpec.async_cached_type_available',
        AsyncMock(return_value=True),
    )

    setup_task = asyncio.create_task(async_setup_entry(hass, config_entry))
    try:
        await asyncio.wait_for(cloud_started.wait(), timeout=0.2)
        result = await asyncio.wait_for(asyncio.shield(setup_task), timeout=0.05)
        assert result is True
        assert entry.cloud_ready is False
        assert len(hass.data[DOMAIN]['test-entry']['configs']) == 1
        forward_setups.assert_awaited_once()
    finally:
        if not setup_task.done():
            setup_task.cancel()
        await asyncio.gather(setup_task, return_exceptions=True)
        if entry.cloud_bootstrap_task and not entry.cloud_bootstrap_task.done():
            entry.cloud_bootstrap_task.cancel()
            await asyncio.gather(entry.cloud_bootstrap_task, return_exceptions=True)
        await config_entry.cancel_background_tasks()


@pytest.mark.asyncio
async def test_cloud_eventually_returns_without_duplicate_local_setup(monkeypatch):
    cloud_started = asyncio.Event()
    cloud_released = asyncio.Event()

    async def delayed_auth(*_args, **_kwargs):
        cloud_started.set()
        await cloud_released.wait()
        return True

    local = cached_device()
    cloud = SimpleNamespace(user_id='test-user', async_check_auth=delayed_auth)
    scale = cloud_scale()
    entry = FakeEntry(
        cloud,
        {local['did']: local},
        {local['did']: local, scale['did']: scale},
    )
    entry.adders['button'] = object()
    config_entry = FakeConfigEntry()
    hass = SimpleNamespace(
        data={DOMAIN: {'accounts': {}}},
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.HassEntry.init',
        lambda _hass, _entry: entry,
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.MiotSpec.async_cached_type_available',
        AsyncMock(return_value=True),
    )

    assert await asyncio.wait_for(async_setup_entry(hass, config_entry), 0.1) is True
    await asyncio.wait_for(cloud_started.wait(), 0.1)
    assert entry.cloud_ready is False
    assert len(entry.created) == 1

    cloud_released.set()
    await asyncio.wait_for(entry.cloud_bootstrap_task, 0.1)
    assert entry.cloud_ready is True
    assert [device.info.model for _, device in entry.created] == [
        'dmaker.fan.p33',
        'yunmai.scales.ms104',
    ]
    assert entry.created[1][1].added_domains == ['button']


@pytest.mark.asyncio
async def test_discovery_failure_retries_without_rebuilding_local(monkeypatch):
    local = cached_device()
    cloud = SimpleNamespace(
        user_id='test-user',
        async_check_auth=AsyncMock(return_value=True),
    )
    entry = FakeEntry(cloud, {local['did']: local}, {local['did']: local})
    entry.get_cloud_devices = AsyncMock(
        side_effect=[OSError('discovery unavailable'), {local['did']: local}]
    )
    config_entry = FakeConfigEntry()
    hass = SimpleNamespace(
        data={DOMAIN: {'accounts': {}}},
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.HassEntry.init',
        lambda _hass, _entry: entry,
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.MiotSpec.async_cached_type_available',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr('custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_RETRY_DELAYS', (0,))

    assert await asyncio.wait_for(async_setup_entry(hass, config_entry), 0.1) is True
    await asyncio.wait_for(entry.cloud_bootstrap_task, 0.2)

    assert entry.cloud_ready is True
    assert entry.get_cloud_devices.await_count == 2
    assert len(entry.created) == 1


@pytest.mark.asyncio
async def test_background_cloud_timeout_retries_and_recovers(monkeypatch):
    calls = 0
    entry = SimpleNamespace(cloud_ready=False, cloud_devices={'stale': object()})

    async def bootstrap():
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Future()
        entry.cloud_ready = True

    monkeypatch.setattr('custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_TIMEOUT', 0.01)
    monkeypatch.setattr('custom_components.xiaomi_miot.CLOUD_BOOTSTRAP_RETRY_DELAYS', (0,))

    await asyncio.wait_for(
        async_cloud_bootstrap_with_retry(entry, 'test-account', 1, bootstrap),
        0.2,
    )
    assert calls == 2
    assert entry.cloud_ready is True
    assert entry.cloud_devices is None


@pytest.mark.asyncio
async def test_config_entry_owned_cloud_task_is_cancelled_on_unload():
    config_entry = FakeConfigEntry()
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_forward_entry_unload=AsyncMock(return_value=True)
        )
    )
    entry = HassEntry(hass, config_entry)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_cloud():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    entry.set_cloud_bootstrap(pending_cloud)
    task = entry.start_cloud_bootstrap()
    await asyncio.wait_for(started.wait(), 0.1)
    assert await entry.async_unload() is True

    assert task.cancelled()
    assert cancelled.is_set()
    assert entry.cloud_bootstrap_task is None


@pytest.mark.asyncio
async def test_starting_cloud_bootstrap_twice_keeps_one_active_task():
    config_entry = FakeConfigEntry()
    entry = HassEntry(SimpleNamespace(), config_entry)
    started = [asyncio.Event(), asyncio.Event()]

    def factory(index):
        async def pending_cloud():
            started[index].set()
            await asyncio.Future()
        return pending_cloud

    entry.set_cloud_bootstrap(factory(0))
    first = entry.start_cloud_bootstrap()
    await asyncio.wait_for(started[0].wait(), 0.1)
    entry.set_cloud_bootstrap(factory(1))
    second = entry.start_cloud_bootstrap()
    await asyncio.wait_for(started[1].wait(), 0.1)

    assert first.cancelled()
    assert not second.done()
    assert sum(not task.done() for task in config_entry.background_tasks) == 1
    await entry.async_cancel_cloud_bootstrap()


@pytest.mark.asyncio
async def test_no_usable_local_cache_keeps_cloud_in_setup_path(monkeypatch):
    cloud_started = asyncio.Event()

    async def blackholed_auth(*_args, **_kwargs):
        cloud_started.set()
        await asyncio.Future()

    cloud = SimpleNamespace(user_id='test-user', async_check_auth=blackholed_auth)
    entry = FakeEntry(cloud, {}, {})
    config_entry = FakeConfigEntry()
    hass = SimpleNamespace(
        data={DOMAIN: {'accounts': {}}},
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    monkeypatch.setattr(
        'custom_components.xiaomi_miot.HassEntry.init',
        lambda _hass, _entry: entry,
    )

    setup_task = asyncio.create_task(async_setup_entry(hass, config_entry))
    try:
        await asyncio.wait_for(cloud_started.wait(), 0.1)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(setup_task), 0.05)
    finally:
        setup_task.cancel()
        await asyncio.gather(setup_task, return_exceptions=True)


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
