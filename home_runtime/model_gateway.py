"""Internal compatible endpoint for metered core and optional DSH requests."""
from http.server import ThreadingHTTPServer
import release_paths
from .model import ModelClient
from .gateway_handler import gateway_handler

def run_gateway():
    client = ModelClient(release_paths.workspace())
    server = ThreadingHTTPServer(('127.0.0.1', release_paths.port('model')), gateway_handler(client, release_paths.runtime()['internal_token']))
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        server.server_close()
