"""Compose an owned Harness profile using the official npm CLI and plugins."""
from pathlib import Path
import shutil
import yaml
from .files import atomic_json, read_json, atomic_bytes
from .instance import source_root
import release_paths

def prepare(workspace, runtime, environment=None):
    workspace = Path(workspace)
    install = workspace.parent / 'components/dsh'
    entry = install / 'node_modules/@deepseek-ai/dsh/lib/bin.js'
    if not entry.is_file():
        raise ValueError('请先安装组件包中的 DSH 运行组件。')
    profile = workspace / 'dsh/profiles/web'
    local_plugin = profile / 'node_modules/qq-agent-presets'
    shutil.copytree(workspace / 'qq-bridge/plugins/qq-agent-presets', local_plugin, dirs_exist_ok=True)
    atomic_json(profile / 'package.json', {'name': 'xiaoyu-home-profile', 'private': True,
        'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', 'qq-agent-presets']}}})
    provider = {'displayName': '小鱼之家统一预算接口', 'apiKeyEnv': 'XIAOYU_GATEWAY_TOKEN',
        'api': 'openai-completions', 'baseURL': release_paths.url('model', '/v1'),
        'retryPolicy': {'mode': 'normal', 'maxRetries': 0},
        'defaultContextWindow': 131072, 'defaultMaxTokens': 8192,
        'models': [{'id': 'configured-model', 'name': '入住设置中的聊天模型', 'contextWindow': 131072,
                    'maxTokens': 8192, 'reasoningEfforts': False}]}
    patch = [{'id': 'llm-pi-ai', 'config': {'providers': {'xiaoyu-local': provider}}},
             {'id': 'agent-default-model', 'config': {'provider': 'xiaoyu-local', 'model': 'configured-model'}}]
    for name in ('session-log-deepseek', 'plugin-package-inventory-deepseek', 'session-telemetry-otel',
                 'llm-deepseek', 'llm-deepseek-account', 'session-title-llm'):
        patch.append({'id': name, 'disabled': True})
    node = source_root() / 'runtime/node/node.exe'
    entries = [('snowluma','mcp-snowluma-safe.js'), ('dafeiyu','mcp-dafeiyu.js'),
               ('xiaoyu-voice','mcp-xiaoyu-voice.js'), ('xiaoyu-qzone','mcp-xiaoyu-qzone.js'),
               ('xiaoyu-profile','mcp-xiaoyu-profile.js'), ('xiaoyu-meme','mcp-xiaoyu-meme.js'),
               ('web-search-safe','mcp-web-search-safe.js')]
    inserts = []
    for name, script in entries:
        inserts.append({'id': 'mcp-' + name, 'name': '@deepseek-ai/dsh-mcp-client', 'config': {
            'serverName': name, 'transport': 'stdio', 'command': str(node),
            'args': [str(workspace / 'qq-bridge/src' / script)], 'cwd': str(workspace / 'qq-bridge'),
            'env': {key: value for key, value in (environment or __import__('os').environ).items() if key in
                ('XIAOYU_WORKSPACE', 'XIAOYU_RUNTIME', 'DSH_HOME', 'DAFEIYU_API', 'MEME_API', 'XIAOYU_ONEBOT_TOKEN', 'XIAOYU_ONEBOT_WS_TOKEN')},
            'toolCallTimeoutMs': 180000}})
    patch.append({'insert': inserts})
    atomic_bytes(profile / 'cordis.patch.yml', yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode())
    return [str(node), str(entry), 'web', '--host', '127.0.0.1', '--port', str(runtime['ports']['dsh']), '--no-open']
