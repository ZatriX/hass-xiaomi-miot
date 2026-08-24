import json

from scripts.compatibility_manifest import compare, registry_manifest


def registry(records):
    return {'data': {'entities': records}}


def test_manifest_filters_entry_and_device_entities(tmp_path):
    path = tmp_path / 'core.entity_registry'
    path.write_text(
        json.dumps(registry([
            {
                'config_entry_id': 'target',
                'device_id': 'device',
                'entity_id': 'fan.test',
                'unique_id': 'stable',
                'platform': 'xiaomi_miot',
            },
            {
                'config_entry_id': 'target',
                'device_id': None,
                'entity_id': 'sensor.cloud',
                'unique_id': 'cloud',
            },
            {
                'config_entry_id': 'other',
                'device_id': 'device',
                'entity_id': 'fan.other',
                'unique_id': 'other',
            },
        ])),
        encoding='utf-8',
    )

    manifest = registry_manifest(path, 'target')

    assert len(manifest) == 1
    assert manifest[0]['entity_id'] == 'fan.test'
    assert manifest[0]['unique_id'] == 'stable'


def test_compare_detects_identity_and_metadata_changes():
    before = [{'entity_id': 'fan.test', 'unique_id': 'stable', 'platform': 'xiaomi_miot'}]
    same = [dict(before[0])]
    changed = [{**before[0], 'platform': 'other'}]

    assert compare(before, same)['compatible'] is True
    report = compare(before, changed)
    assert report['compatible'] is False
    assert len(report['changed']) == 1
