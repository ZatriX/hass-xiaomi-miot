import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_get_token_service_is_not_exposed():
    services = (ROOT / 'custom_components/xiaomi_miot/services.yaml').read_text(
        encoding='utf-8'
    )
    setup = (ROOT / 'custom_components/xiaomi_miot/__init__.py').read_text(
        encoding='utf-8'
    )
    assert '\nget_token:' not in f'\n{services}'
    assert "DOMAIN, 'get_token'" not in setup


def test_account_request_log_does_not_include_payload_response_or_cookies():
    source = (
        ROOT / 'custom_components/xiaomi_miot/core/xiaomi_cloud.py'
    ).read_text(encoding='utf-8')
    tree = ast.parse(source)
    account_post = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == 'account_post'
    )
    log_calls = [
        node
        for node in ast.walk(account_post)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'log'
    ]
    assert len(log_calls) == 1
    rendered = ast.dump(log_calls[0])
    assert 'resp.text' not in rendered
    assert 'cookies' not in rendered
    assert 'kwargs' not in rendered


def test_login_errors_and_logs_do_not_embed_auth_payloads():
    source = (
        ROOT / 'custom_components/xiaomi_miot/core/xiaomi_cloud.py'
    ).read_text(encoding='utf-8')
    assert "f'Login to xiaomi error: {response.text}" not in source
    assert "[auth, self.cookies]" not in source
    assert "'cookies': cookies.get_dict()" not in source
    assert "'response': response.text" not in source
    assert "{**post, 'hash': '*'" not in source


def test_setup_and_config_flow_logs_do_not_render_config_or_tokens():
    setup = (ROOT / 'custom_components/xiaomi_miot/__init__.py').read_text(
        encoding='utf-8'
    )
    flow = (ROOT / 'custom_components/xiaomi_miot/config_flow.py').read_text(
        encoding='utf-8'
    )
    assert "'config': config" not in setup
    assert "{**cfg, CONF_TOKEN" not in setup
    assert "'user_input': user_input" not in flow
    assert "'miio_info': info" not in flow
