import ipaddress


def cached_device_token(data: dict) -> str:
    """Return a valid local MiIO token without exposing it to logs."""
    candidates = [
        data.get('token'),
        (data.get('extra') or {}).get('token'),
        (data.get('miio_info') or {}).get('token'),
    ]
    for token in candidates:
        if not isinstance(token, str) or len(token) != 32:
            continue
        try:
            bytes.fromhex(token)
        except ValueError:
            continue
        return token
    return ''


def cached_device_host(data: dict) -> str:
    host = data.get('localip') or data.get('host') or ''
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return ''


def is_local_cache_candidate(data: dict, local_models) -> bool:
    """Validate the persisted fields required for a local cold start."""
    return bool(
        data.get('model') in local_models
        and cached_device_host(data)
        and cached_device_token(data)
        and (data.get('spec_type') or data.get('urn'))
    )
