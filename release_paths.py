"""Paths and ports for one explicitly isolated release instance."""
from pathlib import Path
import json
import os

def workspace():
    if not os.environ.get('XIAOYU_WORKSPACE'):
        raise RuntimeError('请通过小鱼之家发行版启动器运行。')
    return Path(os.environ['XIAOYU_WORKSPACE']).resolve()

def path(relative=''):
    root = workspace()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('路径必须位于当前实例内')
    return str(target)

def runtime():
    return json.loads(Path(os.environ['XIAOYU_RUNTIME']).read_text(encoding='utf-8'))

def port(service):
    return int(runtime()['ports'][service])

def url(service, suffix=''):
    state = runtime()
    configured = state.get('integrations', {}).get(service, '')
    return (configured.rstrip('/') if configured else 'http://127.0.0.1:' + str(state['ports'][service])) + suffix
