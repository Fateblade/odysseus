"""Request content, caller identity, rotation and observer coverage."""

import asyncio
import importlib.util
import json
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.operations import audit

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def callback(tmp_path, monkeypatch):
    monkeypatch.setenv('ODYSSEUS_AUDIT_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('ODYSSEUS_AUDIT_CAPTURE_CONTENT', 'true')
    parent = types.ModuleType('litellm.integrations.custom_logger')
    parent.CustomLogger = object
    monkeypatch.setitem(sys.modules, 'litellm.integrations.custom_logger', parent)
    monkeypatch.setitem(sys.modules, 'odysseus_audit_store', audit)
    return load('gateway_audit_test', 'scripts/litellm_audit.py').RequestAudit()


def test_content_detail_is_private_and_summary_excludes_it(tmp_path):
    entry_id = audit.emit(tmp_path, 'request.received', actor='seth', content={'messages': ['full prompt']},
                          api_key='must-not-be-stored', endpoint='https://user:password@host:8000/v1?api_key=secret#secret')
    summary = audit.read(tmp_path)['events'][0]
    assert 'content' not in summary
    entry = audit.read(tmp_path, entry_id)['event']
    assert entry['content']['messages'] == ['full prompt']
    assert entry['endpoint'] == 'https://host:8000/v1'
    assert 'must-not-be-stored' not in json.dumps(entry)
    assert 'password' not in json.dumps(entry)


def test_rotated_entries_remain_readable_and_damage_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'MAX_BYTES', 180)
    ids = [audit.emit(tmp_path, 'test', actor='caller', content='x' * 40) for _ in range(4)]
    assert audit.read(tmp_path, ids[0])['event']['id'] == ids[0]
    with (tmp_path / 'events.jsonl').open('a') as file:
        file.write('not json\n[]\n{}\n')
    result = audit.read(tmp_path)
    assert len(result['events']) == 4
    assert result['skipped_lines'] == 3


def test_large_content_is_explicitly_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'CONTENT_BYTES', 100)
    entry_id = audit.emit(tmp_path, 'test', content={'prompt': 'x' * 500})
    captured = audit.read(tmp_path, entry_id)['event']['content']
    assert captured['truncated'] is True
    assert len(captured['captured_text']) == 100
    assert captured['original_json_bytes'] > 500


def test_concurrent_writers_keep_complete_records(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda index: audit.emit(tmp_path, 'test', actor=str(index)), range(30)))
    assert len(set(ids)) == 30
    assert len(audit.read(tmp_path)['events']) == 30


def test_gateway_stream_captures_final_content_and_trusted_identity(callback, tmp_path):
    async def run():
        data = {'model': 'alias', 'stream': True, 'user': 'claimed-admin', 'messages': [{'role': 'user', 'content': 'full prompt'}],
                'metadata': {'odysseus_audit': {'actor': 'forged', 'request_id': 'forged'}, 'requester_ip_address': '127.0.0.1'},
                'proxy_server_request': {'headers': {'Authorization': 'Bearer secret-key', 'X-Client-Name': 'coding-agent'}}}
        data = await callback.async_pre_call_hook({'user_id': 'authenticated-seth', 'key_alias': 'desktop'}, None, data, 'completion')
        kwargs = {'model': 'backend/model', 'stream': True, 'litellm_params': {'metadata': data['metadata'],
                  'api_base': 'http://user:password@localhost:8000/v1', 'custom_llm_provider': 'openai'},
                  'complete_streaming_response': {'choices': [{'message': {'content': 'full response', 'tool_calls': [{'id': 'tool-1'}]}}],
                                                  'usage': {'prompt_tokens': 0, 'completion_tokens': 12}}}
        await callback.async_log_pre_api_call('backend/model', [], kwargs)
        now = datetime.now()
        await callback.async_log_success_event(kwargs, {}, now, now + timedelta(seconds=2))
    asyncio.run(run())
    events = audit.read(tmp_path)['events']
    assert len({entry['request_id'] for entry in events}) == 1
    assert all(entry['actor'] == 'authenticated-seth' for entry in events)
    assert all(entry['client_label'] == 'coding-agent' for entry in events)
    success = next(entry for entry in events if entry['event'] == 'request.succeeded')
    assert success['prompt_tokens'] == 0
    assert success['output_tokens'] == 12
    assert success['duration_ms'] == 2000
    assert audit.read(tmp_path, success['id'])['event']['content']['choices'][0]['message']['content'] == 'full response'
    assert 'secret-key' not in (tmp_path / 'events.jsonl').read_text()
    assert 'password' not in (tmp_path / 'events.jsonl').read_text()


def test_gateway_failure_does_not_log_exception_secrets(callback, tmp_path):
    async def run():
        data = await callback.async_pre_call_hook({}, None, {'model': 'test', 'prompt': 'hello'}, 'completion')
        await callback.async_post_call_failure_hook(data, RuntimeError('Bearer secret-key'), {})
    asyncio.run(run())
    failed = next(row for row in audit.read(tmp_path)['events'] if row['event'] == 'request.failed')
    assert failed['error_type'] == 'RuntimeError'
    assert 'secret-key' not in (tmp_path / 'events.jsonl').read_text()


def test_gateway_capture_can_be_disabled(callback, tmp_path):
    callback.capture = False
    asyncio.run(callback.async_pre_call_hook({}, None, {'model': 'test', 'prompt': 'private'}, 'completion'))
    assert 'private' not in (tmp_path / 'events.jsonl').read_text()


def test_observer_preserves_last_known_state_without_inventing_caller(tmp_path):
    monitor = load('observer_test', 'scripts/odysseus-audit-monitor.py')
    state = monitor.record_transitions(tmp_path, {}, {'ollama': []})
    state = monitor.record_transitions(tmp_path, state, {'ollama': [{'model': 'qwen', 'context': 4096}]})
    state = monitor.record_transitions(tmp_path, state, {'ollama': None})
    monitor.record_transitions(tmp_path, state, {'ollama': [{'model': 'qwen', 'context': 4096}]})
    events = audit.read(tmp_path)['events']
    assert sum(row['event'] == 'residency.changed' for row in events) == 1
    assert all('actor' not in row and 'request_id' not in row for row in events)


def test_operations_propagate_actor_and_failure(tmp_path):
    management = types.SimpleNamespace(settings={'audit_directory': str(tmp_path)})
    payload = {'action': 'manage', 'target': 'ollama', 'model': 'qwen', '_audit': {'actor': 'seth', 'request_id': 'req1'}}
    def failed():
        raise ValueError('private failure')
    with pytest.raises(ValueError):
        audit.run_operation(management, payload, failed)
    events = audit.read(tmp_path)['events']
    assert {entry['event'] for entry in events} == {'operation.requested', 'operation.failed'}
    assert all(entry['actor'] == 'seth' and entry['model'] == 'qwen' for entry in events)
    assert management.audit_actor['request_id'] == 'req1'


def test_gateway_install_plan_preserves_settings_and_refuses_orchestrators(tmp_path):
    installer = load('audit_installer_test', 'scripts/odysseus-install-gateway-audit.py')
    existing = {'Config': {'Image': 'original', 'Env': ['API_KEY=private', 'OTHER=value'], 'Cmd': ['--config', '/app/config.yaml'],
                           'Entrypoint': ['gateway'], 'Labels': {}},
                'HostConfig': {'NetworkMode': 'host', 'Binds': ['/home/seth/litellm:/app/config'], 'RestartPolicy': {'Name': 'always'}},
                'Mounts': []}
    body = installer.plan(existing, 'audited-image', tmp_path, 1000, 1000)
    assert body['Cmd'] == existing['Config']['Cmd']
    assert body['Entrypoint'] == ['gateway']
    assert 'API_KEY=private' in body['Env']
    assert 'ODYSSEUS_AUDIT_CAPTURE_CONTENT=true' in body['Env']
    assert body['HostConfig']['RestartPolicy'] == {'Name': 'always'}
    assert len(existing['HostConfig']['Binds']) == 1
    existing['Mounts'] = [{'Destination': '/app'}]
    with pytest.raises(ValueError, match='hides the callback'):
        installer.plan(existing, 'image', tmp_path, 1000, 1000)
    existing['Mounts'] = []
    existing['Config']['Labels']['com.docker.compose.project'] = 'managed'
    with pytest.raises(ValueError, match='owning Compose'):
        installer.plan(existing, 'image', tmp_path, 1000, 1000)


def test_admin_routes_override_claimed_actor_and_protect_content(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes import operations_routes
    captured = []
    class Host:
        async def run(self, action, payload):
            captured.append((action, payload))
            return {'events': []}
    app = FastAPI()
    app.include_router(operations_routes.setup_operations_routes(Host()))
    def admin(request):
        request.state.current_user = 'authenticated-admin'
    monkeypatch.setattr(operations_routes, 'require_admin', admin)
    with TestClient(app) as client:
        assert client.post('/api/operations/audit/detail', json={'id': 'a' * 32, '_audit': {'actor': 'forged'}}).status_code == 200
        assert captured[-1][1]['_audit']['actor'] == 'authenticated-admin'
        def denied(request):
            raise HTTPException(403, 'Admin only')
        monkeypatch.setattr(operations_routes, 'require_admin', denied)
        assert client.get('/api/operations/audit').status_code == 403
        assert client.post('/api/operations/audit/detail', json={'id': 'a' * 32}).status_code == 403


@pytest.mark.parametrize('fail_start', [False, True])
def test_gateway_install_applies_and_restores_yaml_on_start_failure(tmp_path, monkeypatch, fail_start):
    installer = load('audit_install_lifecycle', 'scripts/odysseus-install-gateway-audit.py')
    config = tmp_path / 'config.yaml'
    original = b'model_list: []\nlitellm_settings:\n  callbacks: existing.logger\n'
    config.write_bytes(original)
    inspected = {'Name': '/litellm', 'Config': {'Env': [], 'Labels': {}},
                 'HostConfig': {'NetworkMode': 'host'}, 'Mounts': [{'Source': str(config), 'Destination': '/app/config.yaml'}]}
    calls = []
    platform = types.SimpleNamespace(**vars(installer.os))
    platform.name, platform.getuid, platform.getgid = 'posix', lambda: 1000, lambda: 1000
    monkeypatch.setattr(installer, 'os', platform)
    monkeypatch.setattr(installer, 'socket', types.SimpleNamespace(AF_UNIX=1))
    monkeypatch.setattr(installer.subprocess, 'check_output', lambda args: json.dumps([inspected]).encode())
    def run(args, **kwargs):
        calls.append(args)
        if fail_start and args == ['docker', 'start', 'litellm'] and calls.count(args) == 1:
            raise RuntimeError('start failure')
    monkeypatch.setattr(installer.subprocess, 'run', run)
    monkeypatch.setattr(installer, 'docker_api', lambda *args: {})
    monkeypatch.setattr(sys, 'argv', ['installer', '--image', 'audited', '--config', str(config),
                                              '--audit-directory', str(tmp_path / 'audit'), '--apply'])
    if fail_start:
        with pytest.raises(RuntimeError, match='start failure'):
            installer.main()
        assert config.read_bytes() == original
        assert ['docker', 'rm', '-f', 'litellm'] in calls
    else:
        installer.main()
        assert 'existing.logger' in config.read_text()
        assert 'odysseus_audit.audit_logger' in config.read_text()
    assert list(tmp_path.glob('*.config.yaml'))[0].read_bytes() == original


def test_gateway_overwrites_both_metadata_variants(callback, tmp_path):
    forged = {'odysseus_audit': {'actor': 'forged', 'request_id': 'forged'}}
    data = {'model': 'alias', 'metadata': forged.copy(), 'litellm_metadata': forged.copy(),
            'litellm_params': {'metadata': forged.copy()}}
    result = asyncio.run(callback.async_pre_call_hook({'user_id': 'verified'}, None, data, 'completion'))
    assert result['metadata']['odysseus_audit']['actor'] == 'verified'
    assert result['litellm_metadata']['odysseus_audit']['actor'] == 'verified'
    assert result['litellm_params']['metadata']['odysseus_audit']['actor'] == 'verified'
    assert audit.read(tmp_path)['events'][0]['actor'] == 'verified'
