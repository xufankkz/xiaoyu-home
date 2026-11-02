from pathlib import Path
import importlib.util
import json
import os
import runpy
import sys
from http.server import ThreadingHTTPServer
from .auth import install_opener, internal_handler
import release_paths

SERVICES = {'web': 'xiaoyu-web', 'qzone': 'qzone-service', 'diary': 'diary-service', 'memes': 'meme-service', 'media': 'media-service'}

def run(name):
    install_opener()
    workspace = release_paths.workspace()
    for folder in (workspace / 'dafeiyu', workspace / 'xiaoyu-web'):
        sys.path.insert(0, str(folder))
    if name == 'core':
        from core import api
        api.Handler = internal_handler(api.Handler)
        runpy.run_path(str(workspace / 'dafeiyu/release_core.py'), run_name='__main__')
        return 0
    if name == 'model':
        from .model_gateway import run_gateway
        return run_gateway()
    path = workspace / SERVICES[name] / 'server.py'
    spec = importlib.util.spec_from_file_location('xiaoyu_' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.PORT = release_paths.port(name)
    if name == 'web':
        from .api import install
        install(module)
        return module.main()
    module.Handler = internal_handler(module.Handler)
    server = ThreadingHTTPServer(('127.0.0.1', module.PORT), module.Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        server.server_close()
