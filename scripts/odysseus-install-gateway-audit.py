#!/usr/bin/env python3
"""Recreate a standalone host-network gateway while preserving its Docker settings."""

import argparse
import copy
import http.client
import json
import os
import socket
import subprocess
import time
from io import StringIO
from urllib.parse import quote
from pathlib import Path


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect('/var/run/docker.sock')


def docker_api(method, path, body):
    connection = DockerConnection('localhost', timeout=60)
    try:
        connection.request(method, path, json.dumps(body), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read()
        if response.status >= 300:
            raise RuntimeError(f'Docker API failed with status {response.status}; retained gateway backup is available.')
        return json.loads(raw) if raw else {}
    finally:
        connection.close()


def plan(inspect, image, folder, uid, gid):
    host = inspect['HostConfig']
    if host.get('NetworkMode') != 'host' or host.get('AutoRemove'):
        raise ValueError('This installer requires a persistent host-network container.')
    labels = inspect['Config'].get('Labels') or {}
    if any(key.startswith(('com.docker.compose.', 'com.docker.swarm.')) for key in labels):
        raise ValueError('Update the owning Compose/Swarm configuration instead of cloning its container.')
    if any(mount.get('Destination') == '/var/log/odysseus-audit' for mount in inspect.get('Mounts', [])):
        raise ValueError('The audit mount already exists; update the existing deployment instead.')
    hidden = {'/', '/app', '/app/odysseus_audit.py', '/app/odysseus_audit_store.py'}
    if any(mount.get('Destination', '').rstrip('/') in hidden or mount.get('Destination') == '/'
           for mount in inspect.get('Mounts', [])):
        raise ValueError('An existing mount hides the callback modules; install them through the owning deployment.')
    user = inspect['Config'].get('User', '')
    if user not in ('', 'root', '0', '0:0', str(uid), f'{uid}:{gid}'):
        raise ValueError('Container user differs from the host user; configure shared audit permissions explicitly.')
    body = copy.deepcopy(inspect['Config'])
    body['Image'] = image
    body['HostConfig'] = copy.deepcopy(host)
    body['HostConfig']['Binds'] = list(host.get('Binds') or []) + [f'{folder}:/var/log/odysseus-audit:rw']
    env = {'ODYSSEUS_AUDIT_DIRECTORY': '/var/log/odysseus-audit', 'ODYSSEUS_AUDIT_CAPTURE_CONTENT': 'true',
           'ODYSSEUS_AUDIT_OWNER_UID': str(uid), 'ODYSSEUS_AUDIT_OWNER_GID': str(gid)}
    body['Env'] = [value for value in body.get('Env', []) if value.split('=', 1)[0] not in env]
    body['Env'].extend(f'{key}={value}' for key, value in env.items())
    return body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--container', default='litellm')
    parser.add_argument('--image', required=True)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--audit-directory', type=Path, default=Path.home() / '.local/share/odysseus-operations/audit')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix' or not hasattr(socket, 'AF_UNIX'):
        parser.error('Run this script as the SSH user on the Linux Docker host.')
    inspected = json.loads(subprocess.check_output(['docker', 'inspect', args.container]))[0]
    if inspected.get('Name', '').lstrip('/') != args.container:
        parser.error('Use the container name, not its ID.')
    config = args.config.expanduser().resolve(strict=True)
    mounted_paths = []
    for mount in inspected.get('Mounts', []):
        source = Path(mount['Source']).resolve()
        if config == source or source in config.parents:
            relative = config.relative_to(source) if config != source else Path()
            mounted_paths.append(Path(mount['Destination']) / relative)
    if not mounted_paths or any(path.parent != Path('/app') for path in mounted_paths):
        parser.error('Expected the selected configuration mounted directly under /app; place callback modules beside other config paths manually.')
    original = config.read_bytes()
    from ruamel.yaml import YAML
    yaml = YAML()
    document = yaml.load(original.decode())
    settings = document.setdefault('litellm_settings', {})
    callbacks = settings.get('callbacks') or []
    if isinstance(callbacks, str):
        callbacks = [callbacks]
    if not isinstance(callbacks, list):
        parser.error('Existing callbacks must be a string or list.')
    callback = 'odysseus_audit.audit_logger'
    if callback not in callbacks:
        callbacks.append(callback)
    settings['callbacks'] = callbacks
    output = StringIO()
    yaml.dump(document, output)
    edited = output.getvalue().encode()
    folder = args.audit_directory.expanduser().resolve()
    body = plan(inspected, args.image, folder, os.getuid(), os.getgid())
    subprocess.run(['docker', 'image', 'inspect', args.image], stdout=subprocess.DEVNULL, check=True)
    print(f'Preserve container environment, command, entrypoint, mounts, restart policy and host networking.\n'
          f'Use image {args.image}; enable full content logging in {folder}.')
    if not args.apply:
        print('No changes made. Add --apply after following the configuration backup steps in the guide.')
        return
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = f'{args.container}-before-audit-{int(time.time())}'
    path = folder.parent / f'{backup}.inspect.json'
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
        json.dump(inspected, file)
    config_backup = folder.parent / f'{backup}.config.yaml'
    with os.fdopen(os.open(config_backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as file:
        file.write(original)
    subprocess.run(['docker', 'stop', args.container], check=True)
    renamed = False
    created = False
    config_written = False
    try:
        if config.read_bytes() != original:
            raise RuntimeError('Gateway configuration changed during preparation.')
        config_written = True
        with config.open('r+b') as file:
            file.write(edited)
            file.truncate()
            file.flush()
            os.fsync(file.fileno())
        subprocess.run(['docker', 'rename', args.container, backup], check=True)
        renamed = True
        docker_api('POST', '/containers/create?name=' + quote(args.container, safe=''), body)
        created = True
        subprocess.run(['docker', 'start', args.container], check=True)
    except Exception:
        if config_written:
            with config.open('r+b') as file:
                file.write(original)
                file.truncate()
                file.flush()
                os.fsync(file.fileno())
        if created:
            subprocess.run(['docker', 'rm', '-f', args.container], check=False)
        if renamed:
            subprocess.run(['docker', 'rename', backup, args.container], check=True)
        subprocess.run(['docker', 'start', args.container], check=True)
        raise
    print(f'Previous container retained as {backup}. Check liveness and a real logged request now.\n'
          f'YAML backup: {config_backup}\n'
          'A started container is not proof of healthy inference. Use the guide to restore the YAML and container if needed.')


if __name__ == '__main__':
    main()
