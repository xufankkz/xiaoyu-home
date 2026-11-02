"""Materialize versioned program files; initialize missing configuration only."""
from pathlib import Path
import hashlib
import json
import os
import secrets
import shutil
import socket
from . import VERSION
from .files import atomic_json, atomic_bytes, read_json

SOURCE_GROUPS = ('dafeiyu', 'xiaoyu-web', 'qq-bridge', 'qzone-service', 'diary-service', 'meme-service', 'media-service')
RUNTIME_DIRS = {'data', 'state', 'logs', 'output', '__pycache__', '.venv', '.git'}

def _filesystem_path(path):
    """Use extended Win32 paths when copying deeply nested dependencies."""
    path = Path(path).resolve()
    if os.name != 'nt':
        return path
    value = str(path)
    if value.startswith('\\\\?\\'):
        return path
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)

def source_root():
    import sys
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / ('source' if getattr(sys, 'frozen', False) else '')

def default_data_dir():
    base = os.environ.get('LOCALAPPDATA')
    return (Path(base) if base else Path.home() / '.local/share') / 'XiaoyuHome'

def reserve_ports():
    # Hold all reservations at once; the supervisor starts workers immediately afterward.
    sockets, result = [], {}
    try:
        for name in ('web', 'core', 'qzone', 'diary', 'memes', 'media', 'bridge', 'model', 'dsh', 'tts', 'onebot'):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sockets.append(sock)
            result[name] = sock.getsockname()[1]
        return result
    finally:
        for sock in sockets:
            sock.close()

def materialize(data_dir):
    data_dir = Path(data_dir).resolve()
    workspace = data_dir / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    root = _filesystem_path(source_root())
    changed = []
    for name in SOURCE_GROUPS:
        for source in (root / name).rglob('*'):
            relative = source.relative_to(root / name)
            excluded = {'__pycache__', '.git'} if 'node_modules' in relative.parts else RUNTIME_DIRS
            if not source.is_file() or set(relative.parts) & excluded:
                continue
            target = _filesystem_path(workspace / name / relative)
            if target.exists() and (relative.as_posix().startswith('dsh/agent-presets/')
                                    or relative.as_posix().startswith('plugins/qq-agent-presets/presets/')):
                continue
            if not target.exists() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                changed.append((Path(name) / relative).as_posix())
    # No symlinks/junctions to another installation. Optional runtimes remain explicit.
    return workspace, changed

def initialize(data_dir):
    data_dir = Path(data_dir).resolve()
    workspace, changed = materialize(data_dir)
    core_data = workspace / 'dafeiyu/data'
    config_dir = core_data / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    for default in (workspace / 'dafeiyu/config.default').glob('*.json'):
        target = config_dir / default.name
        if not target.exists():
            shutil.copy2(default, target)
    def missing(relative, value):
        target = workspace / relative
        if not target.exists():
            atomic_json(target, value)
    bridge = workspace / 'qq-bridge/config.json'
    if not bridge.exists():
        atomic_json(bridge, read_json(workspace / 'qq-bridge/config.example.json'))
    missing('qq-bridge/state/mode.json', {'mode': 'reserved2'})
    missing('qq-bridge/state/sessions.json', {'sessions': {}})
    missing('qq-bridge/state/social-v2.json', {'conversations': {}})
    missing('astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json', {})
    missing('astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json', {'api_url': '', 'ref_audio_path': '', 'tones': {}})
    missing('xiaoyu-web/data/settings.json', {'appearance': {'theme': 'xiaoyu-room', 'color_mode': 'system', 'night_palette': 'charcoal'}})
    missing('release/settings.json', {'version': VERSION, 'profile': {'name': '', 'owner_address': '', 'description': ''},
             'models': {}, 'integrations': {}, 'automation_enabled': False, 'daily_budget': 0})
    personality = config_dir / 'personality.md'
    if not personality.exists():
        atomic_bytes(personality, b'')
    # Never inherit keys/providers from the current OS account's environment.
    llm_path = config_dir / 'llm.json'
    if not (workspace / 'release/initialized.json').exists():
        llm = read_json(llm_path)
        llm['default'] = 'offline'
        llm['tasks'] = {name: 'offline' for name in llm.get('tasks', {})}
        llm['providers']['http'].update({'base_url': '', 'api_key': '', 'api_key_env': 'XIAOYU_INSTANCE_CHAT_KEY'})
        atomic_json(llm_path, llm)
        missing('release/initialized.json', {'version': VERSION})
    runtime = {'version': VERSION, 'ports': reserve_ports(), 'integrations': {}, 'pid': os.getpid(),
               'internal_token': secrets.token_urlsafe(32)}
    integration = read_json(workspace / 'release/settings.json').get('integrations', {})
    runtime['integrations'] = {key: integration.get(key, {}).get('http_url', '') for key in ('tts', 'dsh', 'onebot')}
    runtime_path = data_dir / 'runtime.json'
    atomic_json(runtime_path, runtime)
    from .settings import synchronize_model
    synchronize_model(workspace, runtime)
    app_path = config_dir / 'app.json'
    app = read_json(app_path)
    app['bridge'].update({'core_emotion_api_port': runtime['ports']['core'], 'emotion_mode': 'core',
                          'dir': str(workspace / 'qq-bridge'), 'enabled': False})
    atomic_json(app_path, app)
    cfg = read_json(bridge)
    cfg['consolePort'] = runtime['ports']['bridge']
    cfg['sessionCwd'] = str(workspace / 'dafeiyu')
    cfg.setdefault('dsh', {})['baseUrl'] = runtime['integrations'].get('dsh') or 'http://127.0.0.1:' + str(runtime['ports']['dsh'])
    atomic_json(bridge, cfg)
    state_dir = workspace / 'qq-bridge/state'
    state_dir.mkdir(parents=True, exist_ok=True)
    token_file = state_dir / 'console-token'
    if not token_file.exists():
        atomic_bytes(token_file, secrets.token_urlsafe(32).encode())
    return workspace, runtime_path, runtime

def environment(workspace, runtime_path, runtime):
    env = os.environ.copy()
    root = source_root()
    env.update(XIAOYU_WORKSPACE=str(workspace), XIAOYU_RUNTIME=str(runtime_path),
               DAFEIYU_HOME=str(workspace / 'dafeiyu/data'), DSH_HOME=str(workspace / 'dsh'),
               DAFEIYU_API='http://127.0.0.1:' + str(runtime['ports']['core']),
               MEME_API='http://127.0.0.1:' + str(runtime['ports']['memes']),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               PYTHONPATH=os.pathsep.join((str(root), str(workspace / 'dafeiyu'), str(workspace / 'xiaoyu-web'))))
    for name in tuple(env):
        if name.upper().endswith(('_API_KEY', '_ACCESS_TOKEN', '_AUTH_TOKEN')) or name.upper() in ('DSH_LAUNCH_TOKEN', 'OPENAI_BASE_URL', 'DSH_HOME'):
            env.pop(name, None)
    env['DSH_HOME'] = str(workspace / 'dsh')
    env['XIAOYU_INSTANCE_CHAT_KEY'] = runtime['internal_token']
    env['XIAOYU_GATEWAY_TOKEN'] = runtime['internal_token']
    from .credentials import Credentials
    credentials = Credentials(workspace / 'release/credentials.dpapi')
    env['XIAOYU_ONEBOT_TOKEN'] = credentials.get('onebot:token')
    env['XIAOYU_ONEBOT_WS_TOKEN'] = credentials.get('onebot:ws_token') or credentials.get('onebot:token')
    node_root = root / 'runtime/node'
    env['PATH'] = str(node_root) + os.pathsep + env.get('PATH', '')
    env['QQBOT_LOCAL_TRANSLATOR_ROOT'] = str(workspace.parent / 'components/translation')
    env['XIAOYU_COMPONENTS'] = str(workspace.parent / 'components')
    return env
