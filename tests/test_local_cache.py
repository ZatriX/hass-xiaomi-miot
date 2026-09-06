from custom_components.xiaomi_miot.core.local_cache import (
    cached_device_host,
    cached_device_token,
    is_local_cache_candidate,
)


LOCAL_MODELS = {
    'xiaomi.vacuum.c102gl',
    'zhimi.heater.mc2a',
    'xiaomi.airp.cpa4',
    'dmaker.fan.p33',
    'deerma.humidifier.jsq2w',
}


def device(model='dmaker.fan.p33', **changes):
    data = {
        'did': 'dummy-device',
        'model': model,
        'localip': '192.0.2.10',
        'token': '00' * 16,
        'spec_type': 'urn:miot-spec-v2:device:fan:0000A005:dmaker-p33:1',
    }
    data.update(changes)
    return data


def test_supported_device_is_eligible():
    assert is_local_cache_candidate(device(), LOCAL_MODELS)


def test_missing_or_invalid_token_is_not_eligible():
    assert not is_local_cache_candidate(device(token=''), LOCAL_MODELS)
    assert not is_local_cache_candidate(device(token='not-a-token'), LOCAL_MODELS)
    assert cached_device_token(device(token='not-a-token')) == ''


def test_missing_or_invalid_host_is_not_eligible():
    assert not is_local_cache_candidate(device(localip=''), LOCAL_MODELS)
    assert not is_local_cache_candidate(device(localip='fan.local'), LOCAL_MODELS)
    assert cached_device_host(device(localip='fan.local')) == ''


def test_missing_spec_type_is_not_eligible():
    assert not is_local_cache_candidate(device(spec_type=''), LOCAL_MODELS)


def test_cloud_only_scale_is_not_eligible():
    assert not is_local_cache_candidate(
        device(model='yunmai.scales.ms104'), LOCAL_MODELS
    )
