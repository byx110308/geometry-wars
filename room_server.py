#!/usr/bin/env python3
"""
几何决斗 - 房间列表服务器
GET  /rooms      → 返回当前活跃房间列表 (JSON)
POST /register   → 注册房间 {code, name, players}
POST /unregister → 移除房间 {code}
"""
import json, time, sys
from http.server import HTTPServer, BaseHTTPRequestHandler

ROOMS = {}
TIMEOUT = 300  # 5分钟无刷新则移除

HOST = '0.0.0.0'
PORT = 8765

class Handler(BaseHTTPRequestHandler):
    def _headers(self, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        if self.path == '/rooms':
            now = time.time()
            for k in list(ROOMS):
                if now - ROOMS[k]['time'] > TIMEOUT:
                    del ROOMS[k]
            self._headers()
            self.wfile.write(json.dumps(list(ROOMS.values()), ensure_ascii=False).encode())
        else:
            self._headers(404)
            self.wfile.write(b'{"error":"not found"}')

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length)) if length > 0 else {}
        except:
            self._headers(400)
            self.wfile.write(b'{"error":"invalid json"}')
            return

        if self.path == '/register':
            code = data.get('code', '').strip().upper()
            if not code:
                self._headers(400)
                self.wfile.write(b'{"error":"missing code"}')
                return
            ROOMS[code] = {
                'code': code,
                'host': data.get('name', '?'),
                'players': data.get('players', 1),
                'time': time.time()
            }
            self._headers()
            self.wfile.write(b'{"ok":true}')

        elif self.path == '/unregister':
            code = data.get('code', '').strip().upper()
            if code in ROOMS:
                del ROOMS[code]
            self._headers()
            self.wfile.write(b'{"ok":true}')

        elif self.path == '/refresh':
            code = data.get('code', '').strip().upper()
            if code in ROOMS:
                ROOMS[code]['time'] = time.time()
                self._headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self._headers(404)
                self.wfile.write(b'{"error":"room not found"}')
        else:
            self._headers(404)
            self.wfile.write(b'{"error":"not found"}')

    def log_message(self, format, *args):
        pass  # 安静模式

if __name__ == '__main__':
    server = HTTPServer((HOST, PORT), Handler)
    print(f'房间服务器已启动: http://{HOST}:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务器已停止')
        server.server_close()
