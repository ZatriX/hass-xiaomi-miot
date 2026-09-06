"""Provide non-polling Xiaomi Miot system health information."""

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .core.hass_entry import HassEntry
from .core.runtime_status import async_cache_snapshot
from .core.utils import async_get_manifest


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info, "/config/integrations")


async def system_health_info(hass: HomeAssistant):
    """Return existing runtime state without Xiaomi or LAN probes."""
    entries = list(HassEntry.ALL.values())
    snapshots = [entry.runtime_snapshot() for entry in entries]
    caches = [await async_cache_snapshot(entry) for entry in entries]

    statuses = sorted({item["status"] for item in snapshots})
    bootstrap_states = sorted({item["bootstrap"]["state"] for item in snapshots})
    refreshes = [
        item["last_successful_refresh"]
        for item in caches
        if item.get("last_successful_refresh")
    ]
    return {
        "component_version": await async_get_manifest(hass, "version", "unknown"),
        "config_entries": len(entries),
        "runtime_status": ", ".join(statuses) if statuses else "unavailable",
        "local_configured_devices": sum(
            item["local_configured_devices"] for item in snapshots
        ),
        "local_reachable_devices": sum(
            item["local_reachable_devices"] for item in snapshots
        ),
        "local_unavailable_devices": sum(
            item["local_unavailable_devices"] for item in snapshots
        ),
        "cloud_only_devices": sum(item["cloud_only_devices"] for item in snapshots),
        "cloud_ready_entries": sum(bool(item["cloud_ready"]) for item in snapshots),
        "bootstrap_state": ", ".join(bootstrap_states) if bootstrap_states else "stopped",
        "last_successful_cache_refresh": max(refreshes) if refreshes else None,
    }
