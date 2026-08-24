import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).parents[1]
UPSTREAM_AST_HASHES = {
    ('custom_components/xiaomi_miot/core/device.py', 'Device', 'unique_id'):
        '51956e4d85f99ade48192e1d0110a21e8237532b5f39b8b49303ecdb62986ae4',
    ('custom_components/xiaomi_miot/core/device.py', 'Device', 'identifiers'):
        '05461ef9690e02fed630f0fb9733a19c2a96441311bfbbb6fd28aeffa2a2570b',
    ('custom_components/xiaomi_miot/core/device.py', 'Device', 'add_entities'):
        '765e8e8494129a7a79a5c9822d37e5019a1319d45b9f75cf33421f1a0aeb0742',
    ('custom_components/xiaomi_miot/core/hass_entity.py', 'XEntity', '__init__'):
        '3b6d33f053e52c8ae87709764179460c428dbd34bd61261392ceaf33b8b6bdbf',
    ('custom_components/xiaomi_miot/core/miot_spec.py', 'MiotSpec', 'generate_entity_id'):
        '4b57dc3fafa7ae691da03bea5af0fbcc36175ec112899082ef48673c98c9f467',
    ('custom_components/xiaomi_miot/core/miot_spec.py', 'MiotSpec', 'generate_entity_id_by_mac'):
        'cbbca682e9e1698d1f26d2c650bbde2dce8ed71bd29f69f75582027526d7a660',
}


def method_hash(path: str, class_name: str, method_name: str) -> str:
    source = (ROOT / path).read_text(encoding='utf-8').replace('\r\n', '\n')
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    return hashlib.sha256(ast.get_source_segment(source, method).encode()).hexdigest()


def test_entity_identity_paths_match_upstream_v1_1_4():
    actual = {
        key: method_hash(*key)
        for key in UPSTREAM_AST_HASHES
    }
    assert actual == UPSTREAM_AST_HASHES
