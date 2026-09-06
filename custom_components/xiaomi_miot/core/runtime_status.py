"""Safe Xiaomi Miot runtime observability helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store



def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def classify_cloud_error(exc: BaseException, phase: str = "") -> str:
    """Map an exception to a safe code without retaining its message."""
    if safe_code := getattr(exc, "safe_code", None):
        return safe_code
    name = type(exc).__name__.lower()
    phase = phase.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return "timeout"
    if "cancel" in name:
        return "cancelled"
    if "needverify" in name or "accessdenied" in name or phase == "auth":
        return "auth_failed"
    if isinstance(exc, OSError) or any(
        marker in name for marker in ("connection", "clienterror", "network")
    ):
        return "network_unreachable"
    if phase == "spec":
        return "spec_failed"
    if phase == "discovery" or "discovery" in name:
        return "discovery_failed"
    return "unknown"


@dataclass
class CloudBootstrapStatus:
    """In-memory state for one config-entry-owned cloud bootstrap."""

    state: str = "idle"
    attempt_count: int = 0
    retry_level: int = 0
    attempt_started_at: datetime | None = None
    attempt_completed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_auth_success_at: datetime | None = None
    last_discovery_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_failure_class: str | None = None
    next_retry_at: datetime | None = None

    def attempt_started(self) -> None:
        self.state = "running"
        self.attempt_count += 1
        self.attempt_started_at = _now()
        self.attempt_completed_at = None
        self.next_retry_at = None

    def auth_succeeded(self) -> None:
        self.last_auth_success_at = _now()

    def discovery_succeeded(self) -> None:
        self.last_discovery_success_at = _now()

    def succeeded(self) -> None:
        now = _now()
        self.state = "ready"
        self.attempt_completed_at = now
        self.last_success_at = now
        self.last_failure_class = None
        self.next_retry_at = None
        self.retry_level = 0

    def failed(self, exc: BaseException, delay: float, phase: str = "") -> None:
        now = _now()
        self.state = "backoff"
        self.attempt_completed_at = now
        self.last_failure_at = now
        self.last_failure_class = classify_cloud_error(exc, phase)
        self.next_retry_at = now + timedelta(seconds=delay)

    def observed_failure(self, exc: BaseException, phase: str = "") -> None:
        """Record a non-bootstrap cloud failure without changing task state."""
        self.last_failure_at = _now()
        self.last_failure_class = classify_cloud_error(exc, phase)

    def stopped(self, cancelled: bool = False) -> None:
        now = _now()
        self.state = "stopped"
        self.attempt_completed_at = now
        self.next_retry_at = None
        if cancelled:
            self.last_failure_at = now
            self.last_failure_class = "cancelled"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = _iso(value)
        return data


def runtime_snapshot(entry) -> dict[str, Any]:
    """Build a safe snapshot from existing runtime state without polling."""
    devices = list(getattr(entry, "devices", {}).values())
    local = [device for device in devices if bool(getattr(device, "use_local", False))]
    reachable = [
        device for device in local if getattr(device, "_local_state", None) is True
    ]
    unavailable = [
        device for device in local if getattr(device, "_local_state", None) is False
    ]
    local_unknown = len(local) - len(reachable) - len(unavailable)

    runtime_cloud_only = [
        device for device in devices if not bool(getattr(device, "use_local", False))
    ]
    cloud_only_count = max(
        len(runtime_cloud_only),
        int(getattr(entry, "cached_cloud_only_devices", 0) or 0),
    )
    cloud_only_available = (
        sum(
            bool(getattr(device, "spec", None))
            and bool(getattr(device, "available", False))
            for device in runtime_cloud_only
        )
        if getattr(entry, "cloud_ready", False)
        else 0
    )

    cache_usable = bool(getattr(entry, "local_cache_usable", False) or local)
    cloud_ready = bool(getattr(entry, "cloud_ready", False))
    local_healthy = bool(reachable) or bool(local and local_unknown)
    all_local_failed = bool(local) and len(unavailable) == len(local)

    if local and local_healthy and not all_local_failed:
        if not cloud_ready:
            status = "local_only"
        elif cloud_only_count > cloud_only_available:
            status = "degraded"
        else:
            status = "local_and_cloud"
    elif not local and cloud_ready:
        status = "cloud_only"
    elif cloud_ready or cache_usable:
        status = "degraded"
    else:
        status = "unavailable"

    entry_state = getattr(getattr(entry, "entry", None), "state", "unknown")
    entry_state = getattr(entry_state, "value", str(entry_state))
    return {
        "status": status,
        "config_entry_state": entry_state,
        "local_cache_usable": cache_usable,
        "local_configured_devices": len(local),
        "local_reachable_devices": len(reachable),
        "local_unavailable_devices": len(unavailable),
        "local_unknown_devices": local_unknown,
        "cloud_only_devices": cloud_only_count,
        "cloud_only_available_devices": cloud_only_available,
        "cloud_ready": cloud_ready,
        "bootstrap": entry.cloud_bootstrap_status.as_dict(),
    }


async def async_cache_snapshot(entry) -> dict[str, Any]:
    """Read safe persisted-cache facts without constructing a cloud session."""
    config = entry.get_config()
    user_id = str(config.get("user_id") or "")
    server = str(config.get("server_country") or "cn")
    payload: dict[str, Any] = {}
    if user_id:
        store = Store(entry.hass, 1, f"xiaomi_miot/devices-{user_id}-{server}.json")
        try:
            payload = await store.async_load() or {}
        except (ValueError, OSError, HomeAssistantError):
            payload = {}
    metadata = payload.get("refresh_metadata") or {}
    devices = payload.get("devices") or []
    return {
        "usable": bool(getattr(entry, "local_cache_usable", False)),
        "schema_version": metadata.get("schema_version"),
        "source": metadata.get("source"),
        "integration_version": metadata.get("integration_version"),
        "server": metadata.get("server") or server,
        "account_scope_present": bool(metadata.get("account_user_id") or user_id),
        "last_successful_refresh": metadata.get("last_successful_cloud_refresh"),
        "cached_devices_count": len(devices),
    }
