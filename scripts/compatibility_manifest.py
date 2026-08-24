"""Snapshot and compare Xiaomi Miot entity-registry compatibility data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


FIELDS = (
    'entity_id',
    'unique_id',
    'platform',
    'device_id',
    'device_class',
    'original_device_class',
    'unit_of_measurement',
    'original_unit_of_measurement',
    'supported_features',
    'capabilities',
    'disabled_by',
    'hidden_by',
)


def registry_manifest(path: Path, entry_id: str) -> list[dict]:
    """Return stable device-bound records for one config entry."""
    payload = json.loads(path.read_text(encoding='utf-8'))
    entities = payload.get('data', {}).get('entities', [])
    return [
        {field: entity.get(field) for field in FIELDS}
        for entity in sorted(entities, key=lambda item: item.get('entity_id', ''))
        if entity.get('config_entry_id') == entry_id and entity.get('device_id')
    ]


def digest(manifest: list[dict]) -> str:
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def manifest_keys(manifest: list[dict]) -> dict[tuple[str, str], dict]:
    return {
        (record['entity_id'], record['unique_id']): record
        for record in manifest
    }


def compare(before: list[dict], after: list[dict]) -> dict:
    """Compare identity and metadata, returning a JSON-serializable report."""
    old = manifest_keys(before)
    new = manifest_keys(after)
    common = old.keys() & new.keys()
    changed = [
        {'key': key, 'before': old[key], 'after': new[key]}
        for key in sorted(common)
        if old[key] != new[key]
    ]
    return {
        'compatible': old.keys() == new.keys() and not changed,
        'before_count': len(before),
        'after_count': len(after),
        'before_sha256': digest(before),
        'after_sha256': digest(after),
        'missing': sorted(old.keys() - new.keys()),
        'added': sorted(new.keys() - old.keys()),
        'changed': changed,
    }


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('before', type=Path)
    parser.add_argument('entry_id')
    parser.add_argument('--after', type=Path)
    args = parser.parse_args()

    before = registry_manifest(args.before, args.entry_id)
    result = compare(before, registry_manifest(args.after, args.entry_id)) \
        if args.after else {
            'count': len(before),
            'sha256': digest(before),
            'entities': before,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not args.after or result['compatible'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
