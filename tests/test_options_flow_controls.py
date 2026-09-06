import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components import diagnostics as ha_diagnostics
from homeassistant.components.config.config_entries import (
    OptionManagerFlowIndexView,
)
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from custom_components.xiaomi_miot import DOMAIN
from custom_components.xiaomi_miot import diagnostics as xiaomi_diagnostics
from custom_components.xiaomi_miot.config_flow import OptionsFlowHandler
from custom_components.xiaomi_miot.core.hass_entry import HassEntry


ENTRY_ID = "xiaomi-entry"


class FakeConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_known_entry(self, entry_id):
        assert entry_id == self.entry.entry_id
        return self.entry


def make_flow(service_result=None, service_error=None):
    entry = SimpleNamespace(
        entry_id=ENTRY_ID,
        data={"username": "account"},
        options={},
    )
    services = SimpleNamespace(
        async_call=AsyncMock(
            return_value=service_result,
            side_effect=service_error,
        )
    )
    hass = SimpleNamespace(
        services=services,
        config_entries=FakeConfigEntries(entry),
        async_create_task=lambda coro: asyncio.create_task(coro),
    )
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.flow_id = "flow-id"
    return flow, services


@pytest.fixture(autouse=True)
def clean_hass_entries():
    HassEntry.ALL.pop(ENTRY_ID, None)
    yield
    HassEntry.ALL.pop(ENTRY_ID, None)


@pytest.mark.asyncio
async def test_config_entry_actions_are_available_without_entry_id_input():
    flow, _ = make_flow()

    result = await flow.async_step_init()

    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["menu_options"] == [
        "cloud",
        "refresh_devices",
        "retry_cloud",
    ]
    assert list(result["data_schema"].schema) == ["next_step_id"]
    with pytest.raises(vol.Invalid):
        result["data_schema"](
            {
                "next_step_id": "refresh_devices",
                "config_entry_id": "manual-entry",
            }
        )


@pytest.mark.asyncio
async def test_refresh_action_is_scoped_and_reports_safe_summary():
    summary = {
        "discovered": 8,
        "new": 1,
        "updated": 2,
        "unchanged": 5,
        "failed": 1,
        "reloaded": True,
    }
    flow, services = make_flow(service_result=summary)

    progress = await flow.async_step_refresh_devices()
    assert progress["type"] is data_entry_flow.FlowResultType.SHOW_PROGRESS
    await flow._refresh_task
    done = await flow.async_step_refresh_devices()

    assert done["type"] is data_entry_flow.FlowResultType.SHOW_PROGRESS_DONE
    assert done["step_id"] == "refresh_complete"
    result = await flow.async_step_refresh_complete()
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "refresh_complete"
    assert result["description_placeholders"] == {
        "new": "1",
        "updated": "2",
        "unchanged": "5",
        "failed": "1",
        "reloaded": "true",
    }
    services.async_call.assert_awaited_once_with(
        DOMAIN,
        "renew_devices",
        {"config_entry_id": ENTRY_ID},
        blocking=True,
        return_response=True,
    )


@pytest.mark.asyncio
async def test_refresh_failure_is_generic_and_does_not_expose_exception(caplog):
    flow, _ = make_flow(
        service_error=HomeAssistantError("credential-bearing response")
    )

    await flow.async_step_refresh_devices()
    await asyncio.sleep(0)
    done = await flow.async_step_refresh_devices()
    result = await flow.async_step_refresh_failed()

    assert done["step_id"] == "refresh_failed"
    assert result["reason"] == "refresh_failed"
    assert "credential-bearing response" not in caplog.text
    assert "HomeAssistantError" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_result", "reason"),
    [
        (
            {
                "status": "started",
                "cloud_ready": False,
                "bootstrap_state": "running",
            },
            "retry_started",
        ),
        (
            {
                "status": "already_running",
                "cloud_ready": False,
                "bootstrap_state": "running",
            },
            "retry_already_running",
        ),
        (
            {
                "status": "failed",
                "cloud_ready": False,
                "bootstrap_state": "stopped",
            },
            "retry_failed",
        ),
    ],
)
async def test_retry_action_is_scoped_and_reports_status(service_result, reason):
    flow, services = make_flow(service_result=service_result)

    result = await flow.async_step_retry_cloud()

    assert result["reason"] == reason
    services.async_call.assert_awaited_once_with(
        DOMAIN,
        "retry_cloud",
        {"config_entry_id": ENTRY_ID},
        blocking=True,
        return_response=True,
    )


@pytest.mark.asyncio
async def test_retry_already_ready_does_not_create_another_task():
    flow, services = make_flow()
    HassEntry.ALL[ENTRY_ID] = SimpleNamespace(cloud_ready=True)

    result = await flow.async_step_retry_cloud()

    assert result["reason"] == "retry_already_ready"
    services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failure_is_generic(caplog):
    flow, _ = make_flow(service_error=OSError("sensitive network detail"))

    result = await flow.async_step_retry_cloud()

    assert result["reason"] == "retry_failed"
    assert "sensitive network detail" not in caplog.text
    assert "OSError" in caplog.text


def test_diagnostics_handler_remains_registered():
    hass = SimpleNamespace(
        data={ha_diagnostics._DIAGNOSTICS_DATA: ha_diagnostics.DiagnosticsData()}
    )

    ha_diagnostics._register_diagnostics_platform(
        hass, DOMAIN, xiaomi_diagnostics
    )

    handler = hass.data[ha_diagnostics._DIAGNOSTICS_DATA].platforms[DOMAIN]
    assert (
        handler.config_entry_diagnostics
        is xiaomi_diagnostics.async_get_config_entry_diagnostics
    )


@pytest.mark.asyncio
async def test_options_flow_api_rejects_non_admin_users():
    request = {"hass_user": SimpleNamespace(is_admin=False)}

    with pytest.raises(Unauthorized):
        await OptionManagerFlowIndexView.post(object(), request)


def test_manifest_keeps_truthful_cloud_polling_classification():
    manifest = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components"
            / DOMAIN
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["iot_class"] == "cloud_polling"
