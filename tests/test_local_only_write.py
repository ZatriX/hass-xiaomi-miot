import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xiaomi_miot import (
    DOMAIN,
    SERVICE_TO_METHOD_BASE,
    bind_services_to_entries,
)
from custom_components.xiaomi_miot.core.device import Device


def make_device(*, local_state=True, local=None, cloud=None):
    device = object.__new__(Device)
    device.info = SimpleNamespace(did="test-device")
    device.local = local
    device.cloud = cloud
    device._local_state = local_state
    device.log = logging.getLogger("test.local_only_write")
    device.dispatch = Mock()
    device.decode = Mock(return_value={"indicator_light.on": False})
    return device


@pytest.mark.asyncio
async def test_local_only_write_sends_directly_to_local_transport():
    local = SimpleNamespace(async_send=AsyncMock(return_value=[{
        "did": "test-device",
        "siid": 4,
        "piid": 1,
        "code": 0,
    }]))
    cloud = SimpleNamespace(async_set_props=AsyncMock())
    device = make_device(local=local, cloud=cloud)

    result = await device.async_set_miot_property_local(4, 1, False)

    assert result.is_success
    local.async_send.assert_awaited_once_with("set_properties", [{
        "did": "test-device",
        "siid": 4,
        "piid": 1,
        "value": False,
    }])
    cloud.async_set_props.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_only_write_fails_when_local_transport_is_unavailable():
    device = make_device(local=None, local_state=False)

    result = await device.async_set_miot_property_local(4, 1, False)

    assert not result.is_success
    assert result.error == "Local transport unavailable"


@pytest.mark.asyncio
async def test_local_only_write_never_uses_available_cloud_without_local():
    local = SimpleNamespace(async_send=AsyncMock())
    cloud = SimpleNamespace(async_set_props=AsyncMock())
    device = make_device(local=local, local_state=False, cloud=cloud)

    result = await device.async_set_miot_property_local(4, 1, False)

    assert not result.is_success
    local.async_send.assert_not_awaited()
    cloud.async_set_props.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_only_write_does_not_retry_in_cloud_after_local_exception(
    caplog,
):
    secret = "token=0123456789abcdef0123456789abcdef"
    local = SimpleNamespace(async_send=AsyncMock(side_effect=OSError(secret)))
    cloud = SimpleNamespace(async_set_props=AsyncMock())
    device = make_device(local=local, cloud=cloud)

    result = await device.async_set_miot_property_local(4, 1, False)

    assert not result.is_success
    assert result.error == "Local transport failed"
    local.async_send.assert_awaited_once()
    cloud.async_set_props.assert_not_awaited()
    assert secret not in caplog.text
    assert secret not in str(result.to_json())


@pytest.mark.asyncio
async def test_standard_write_path_is_unchanged_and_can_use_cloud():
    cloud = SimpleNamespace(async_set_props=AsyncMock(return_value=[{
        "did": "test-device",
        "siid": 4,
        "piid": 1,
        "code": 0,
    }]))
    device = make_device(local=None, local_state=False, cloud=cloud)
    device.custom_config_bool = Mock(return_value=False)

    params = [{
        "did": "test-device",
        "siid": 4,
        "piid": 1,
        "value": False,
    }]
    result = await device.async_set_properties(params)

    assert result[0]["code"] == 0
    cloud.async_set_props.assert_awaited_once_with(params)


@pytest.mark.asyncio
async def test_registered_local_only_service_invokes_explicit_primitive(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entity = SimpleNamespace(
        entity_id="fan.test",
        async_set_miot_property_local=AsyncMock(return_value={"code": 0}),
        async_update_ha_state=AsyncMock(),
    )
    hass.data[DOMAIN] = {"entities": {entity.entity_id: entity}}
    bind_services_to_entries(
        hass,
        {"set_miot_property_local": SERVICE_TO_METHOD_BASE[
            "set_miot_property_local"
        ]},
    )

    try:
        result = await hass.services.async_call(
            DOMAIN,
            "set_miot_property_local",
            {
                "entity_id": entity.entity_id,
                "siid": 4,
                "piid": 1,
                "value": False,
            },
            blocking=True,
            return_response=True,
        )
    finally:
        await hass.async_stop(force=True)

    assert result == {"code": 0}
    entity.async_set_miot_property_local.assert_awaited_once_with(
        siid=4,
        piid=1,
        value=False,
    )
