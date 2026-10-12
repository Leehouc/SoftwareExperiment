"""课程注册系统总入口：python portal.py，访问 http://127.0.0.1:8010。"""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB_ROOT = Path(__file__).resolve().parent / "portal"


class PortalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8")}
        resource = routes.get(urlparse(self.path).path)
        if resource is None:
            self.send_error(404)
            return
        body = (WEB_ROOT / resource[0]).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", resource[1])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    host = os.environ.get("PORTAL_HOST", "127.0.0.1")
    port = int(os.environ.get("PORTAL_PORT", "8010"))
    server = ThreadingHTTPServer((host, port), PortalHandler)
    print(f"课程注册系统总入口：http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
