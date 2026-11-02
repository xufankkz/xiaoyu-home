"""Local session authentication and headers for internal service requests."""
import hmac
import json
import urllib.request
import urllib.parse
from http.cookies import SimpleCookie
import release_paths

def install_opener():
    runtime = release_paths.runtime()
    own_ports = {runtime['ports'][name] for name in ('core', 'web', 'model', 'qzone', 'diary', 'memes', 'media', 'bridge')}
    class InternalHeaders(urllib.request.BaseHandler):
        handler_order = 100
        def http_request(self, request):
            url = urllib.parse.urlsplit(request.full_url)
            if url.hostname == '127.0.0.1' and url.port in own_ports:
                request.add_header('X-Xiaoyu-Internal', runtime['internal_token'])
            return request
        https_request = http_request
    class InternalRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            redirected = super().redirect_request(request, fp, code, message, headers, newurl)
            if redirected is not None:
                url = urllib.parse.urlsplit(newurl)
                if url.hostname != '127.0.0.1' or url.port not in own_ports:
                    redirected.remove_header('X-xiaoyu-internal')
            return redirected
    urllib.request.install_opener(urllib.request.build_opener(InternalHeaders(), InternalRedirects()))

def internal_handler(base):
    expected = release_paths.runtime()['internal_token']
    class InternalHandler(base):
        def permitted(self):
            if self.client_address[0] in ('127.0.0.1', '::1') and hmac.compare_digest(self.headers.get('X-Xiaoyu-Internal', ''), expected):
                return True
            body = b'{"ok":false,"error":"Local service authentication required"}'
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        def do_GET(self):
            if self.permitted():
                return super().do_GET()
        def do_POST(self):
            if self.permitted():
                return super().do_POST()
    return InternalHandler

def session_valid(headers, token):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get('Cookie', ''))
        supplied = cookie['xiaoyu-session'].value if 'xiaoyu-session' in cookie else ''
    except Exception:
        return False
    return hmac.compare_digest(supplied, token)
