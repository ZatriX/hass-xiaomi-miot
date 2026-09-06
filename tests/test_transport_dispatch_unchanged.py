import ast
import hashlib
from pathlib import Path


DEVICE_SOURCE = (
    Path(__file__).parents[1]
    / 'custom_components/xiaomi_miot/core/device.py'
)
UPSTREAM_AST_HASHES = {
    'update_miot_status': '7a5910797d31a8cb5edfac59635239f870f42de40930f94498508a95a7e6f537',
    'async_set_properties': '31ebfab4100d480c0744616c1a991ebd6bd160b318c9ddc9ab3f3530f34beb60',
    'async_call_action': '64594b23c6141857ffe487724ca29b72504292dfad7663ee4898663437adc4f3',
}


def test_read_write_action_dispatch_matches_upstream_v1_1_4():
    tree = ast.parse(DEVICE_SOURCE.read_text(encoding='utf-8'))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in UPSTREAM_AST_HASHES
    }
    source = DEVICE_SOURCE.read_text(encoding='utf-8').replace('\r\n', '\n')
    actual = {
        name: hashlib.sha256(
            ast.get_source_segment(source, node).encode()
        ).hexdigest()
        for name, node in functions.items()
    }
    assert actual == UPSTREAM_AST_HASHES
