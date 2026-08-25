"""Explicit, conservative Xiaomi cloud discovery refresh."""

from __future__ import annotations

import copy
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from homeassistant.exceptions import HomeAssistantError

from .local_cache import (
    cached_device_host,
    cached_device_token,
    is_local_cache_candidate,
)
from .miot_spec import MiotSpec
from .runtime_status import classify_cloud_error

_LOGGER = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 2
CACHE_SOURCE = "cache-first-local-runtime"
INTEGRATION_VERSION = "1.1.4"


class CloudDiscoveryRefreshError(HomeAssistantError):
    """Raised when explicit discovery refresh cannot safely commit."""

    def __init__(self, message: str, safe_code: str = "discovery_failed"):
        super().__init__(message)
        self.safe_code = safe_code


@dataclass
class CloudDiscoveryRefreshResult:
    """Safe operator-facing refresh summary."""

    discovered: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    local_capable_new: int = 0
    cloud_only_new: int = 0
    failed: int = 0
    new_models: list[str] = field(default_factory=list)
    reloaded: bool = False

    def as_dict(self) -> dict:
        """Return only non-secret response fields."""
        return asdict(self)


def _nonempty_fresh_fields(device: dict) -> dict:
    """Return non-sensitive, non-empty discovery facts safe to merge."""
    protected = {
        "did",
        "mac",
        "model",
        "token",
        "localip",
        "host",
        "extra",
        "spec_type",
        "urn",
    }
    return {
        key: value
        for key, value in device.items()
        if key not in protected and value not in (None, "", [], {})
    }


def _raw_token_present(device: dict) -> bool:
    return "token" in device or "token" in (device.get("extra") or {})


def _merge_existing_device(
    old: dict,
    fresh: dict,
    spec_type: str,
    spec_ok: bool,
) -> tuple[dict, bool]:
    """Merge validated mutable facts without erasing known-good credentials."""
    merged = copy.deepcopy(old)
    merged.update(_nonempty_fresh_fields(fresh))

    old_model = old.get("model") or ""
    fresh_model = fresh.get("model") or ""
    invalid = bool(old_model and fresh_model and old_model != fresh_model)
    if not old_model and fresh_model:
        merged["model"] = fresh_model

    raw_host = fresh.get("localip") or fresh.get("host")
    if raw_host:
        if host := cached_device_host(fresh):
            merged["localip"] = host
        else:
            invalid = True

    if _raw_token_present(fresh):
        if token := cached_device_token(fresh):
            merged["token"] = token
        else:
            invalid = True

    if spec_type and spec_ok:
        merged["spec_type"] = spec_type
        merged.pop("urn", None)
    elif (fresh.get("spec_type") or fresh.get("urn")) and not spec_ok:
        invalid = True

    # An invalid mutable fact must never displace the known-good record.
    if invalid:
        return copy.deepcopy(old), False
    return merged, True


async def _refresh_model_spec(
    hass,
    model: str,
    advertised_type: str = "",
) -> tuple[str, bool]:
    """Refresh and validate one model's type, spec, and language cache."""
    spec_type = advertised_type or await MiotSpec.async_get_model_type(hass, model)
    if not spec_type:
        return "", False
    spec = await MiotSpec.async_from_type(hass, spec_type, use_remote=True)
    return spec_type, bool(spec and spec.type == spec_type and spec.services)


async def async_refresh_cloud_discovery(
    hass,
    cloud,
    local_models,
) -> CloudDiscoveryRefreshResult:
    """Refresh discovery and commit a conservative merged cache last."""
    try:
        if not await cloud.async_check_auth(notify=False):
            raise CloudDiscoveryRefreshError(
                "Xiaomi account authentication is unavailable",
                "auth_failed",
            )
    except CloudDiscoveryRefreshError:
        raise
    except Exception as exc:
        raise CloudDiscoveryRefreshError(
            "Xiaomi account authentication failed",
            classify_cloud_error(exc, "auth"),
        ) from None

    old_payload = await cloud.async_load_device_cache_payload()
    old_devices = old_payload.get("devices") or []
    try:
        fresh_payload = await cloud.async_discover_devices()
    except Exception as exc:
        raise CloudDiscoveryRefreshError(
            "Xiaomi cloud discovery failed; the existing cache was not changed",
            classify_cloud_error(exc, "discovery"),
        ) from None

    if not isinstance(fresh_payload, dict):
        raise CloudDiscoveryRefreshError(
            "Xiaomi cloud returned invalid discovery data; "
            "the existing cache was not changed"
        )
    fresh_devices = fresh_payload.get("devices")
    if not isinstance(fresh_devices, list):
        raise CloudDiscoveryRefreshError(
            "Xiaomi cloud returned invalid discovery data; the existing cache was not changed"
        )

    # Refresh the model-to-spec index once. A cached index remains usable if the
    # endpoint is temporarily unavailable; each new local device is still
    # rejected unless its exact spec can be validated below.
    try:
        await MiotSpec.async_get_model_type(hass, "xiaomi.miot.auto", use_remote=True)
    except Exception as exc:
        _LOGGER.warning("Refresh Xiaomi MIoT model index failed: %s", type(exc).__name__)

    old_by_did = {
        device.get("did"): copy.deepcopy(device)
        for device in old_devices
        if isinstance(device, dict) and device.get("did")
    }
    merged_by_did = copy.deepcopy(old_by_did)
    old_without_did = [
        copy.deepcopy(device)
        for device in old_devices
        if not isinstance(device, dict) or not device.get("did")
    ]
    result = CloudDiscoveryRefreshResult(discovered=len(fresh_devices))
    spec_results: dict[tuple[str, str], tuple[str, bool]] = {}
    seen_fresh_dids = set()

    for fresh in fresh_devices:
        if not isinstance(fresh, dict):
            result.failed += 1
            continue
        did = fresh.get("did")
        model = fresh.get("model") or ""
        if not did or not model:
            result.failed += 1
            _LOGGER.warning("Skip invalid Xiaomi discovery candidate: missing did or model")
            continue
        if did in seen_fresh_dids:
            result.failed += 1
            _LOGGER.warning(
                "Skip duplicate Xiaomi discovery candidate: model=%s",
                model,
            )
            continue
        seen_fresh_dids.add(did)

        advertised_type = fresh.get("spec_type") or fresh.get("urn") or ""
        spec_key = (model, advertised_type)
        if spec_key not in spec_results:
            try:
                spec_results[spec_key] = await _refresh_model_spec(
                    hass, model, advertised_type
                )
            except Exception as exc:
                _LOGGER.warning(
                    "Refresh MIoT cache failed for model=%s: %s",
                    model,
                    type(exc).__name__,
                )
                spec_results[spec_key] = (advertised_type, False)
        spec_type, spec_ok = spec_results[spec_key]

        old = old_by_did.get(did)
        if old is not None:
            merged, valid = _merge_existing_device(old, fresh, spec_type, spec_ok)
            if not valid:
                result.failed += 1
                result.unchanged += 1
                _LOGGER.warning(
                    "Preserve cached Xiaomi device after invalid refresh facts: model=%s",
                    model,
                )
                continue
            merged_by_did[did] = merged
            if merged == old:
                result.unchanged += 1
            else:
                result.updated += 1
            continue

        candidate = copy.deepcopy(fresh)
        if spec_type:
            candidate["spec_type"] = spec_type
            candidate.pop("urn", None)
        if model in local_models:
            if not spec_ok or not is_local_cache_candidate(candidate, local_models):
                result.failed += 1
                _LOGGER.warning(
                    "Skip invalid new local Xiaomi candidate: model=%s",
                    model,
                )
                continue
            candidate["localip"] = cached_device_host(candidate)
            candidate["token"] = cached_device_token(candidate)
            result.local_capable_new += 1
        else:
            result.cloud_only_new += 1
            _LOGGER.info("New Xiaomi model remains cloud-only: model=%s", model)

        merged_by_did[did] = candidate
        result.new += 1
        result.new_models.append(model)

    # A device missing from one fresh response is deliberately retained.
    fresh_dids = {
        device.get("did")
        for device in fresh_devices
        if isinstance(device, dict) and device.get("did")
    }
    result.unchanged += len(set(old_by_did) - fresh_dids)

    merged_devices = old_without_did + list(merged_by_did.values())
    result.new_models = sorted(set(result.new_models))
    metadata = {
        **(old_payload.get("refresh_metadata") or {}),
        "schema_version": CACHE_SCHEMA_VERSION,
        "last_successful_cloud_refresh": datetime.now(UTC).isoformat(),
        "integration_version": INTEGRATION_VERSION,
        "source": CACHE_SOURCE,
        "server": cloud.default_server,
        "account_user_id": str(cloud.user_id),
    }
    committed = {
        **old_payload,
        "update_time": datetime.now(UTC).timestamp(),
        "devices": merged_devices,
        "homes": fresh_payload.get("homes") or old_payload.get("homes") or [],
        "refresh_metadata": metadata,
    }
    try:
        await cloud.async_save_device_cache_payload(committed)
    except Exception:
        raise CloudDiscoveryRefreshError(
            "Xiaomi discovery cache commit failed; the previous cache remains active"
        ) from None

    return result
