"""Redacted config-entry diagnostics for Xiaomi Miot."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .core.hass_entry import HassEntry
from .core.runtime_status import async_cache_snapshot


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """Return operational facts without account or device credentials."""
    entry = HassEntry.ALL.get(config_entry.entry_id)
    if not entry:
        state = getattr(config_entry.state, "value", str(config_entry.state))
        return {
            "config_entry": {"state": state, "runtime_loaded": False},
            "runtime": {"status": "unavailable"},
            "cloud": {"cloud_ready": False, "bootstrap": {"state": "stopped"}},
            "cache": {"usable": False},
            "devices": [],
        }

    runtime = entry.runtime_snapshot()
    cache = await async_cache_snapshot(entry)
    devices = []
    for device in entry.devices.values():
        local_capable = bool(getattr(device, "use_local", False))
        devices.append(
            {
                "name": device.name,
                "model": device.model,
                "transport": "local" if local_capable else "cloud",
                "local_reachable": (
                    getattr(device, "_local_state", None) is True
                    if local_capable
                    else None
                ),
                "spec_available": bool(getattr(device, "spec", None)),
            }
        )

    return {
        "config_entry": {
            "state": runtime["config_entry_state"],
            "runtime_loaded": True,
        },
        "runtime": {
            key: value
            for key, value in runtime.items()
            if key not in {"cloud_ready", "bootstrap", "config_entry_state"}
        },
        "cloud": {
            "cloud_ready": runtime["cloud_ready"],
            "bootstrap": runtime["bootstrap"],
        },
        "cache": cache,
        "devices": sorted(devices, key=lambda item: (item["model"], item["name"])),
    }
