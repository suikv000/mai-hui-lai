#!/usr/bin/env python3
"""Local archive demo. Only campaign-calendar is exposed to the browser."""
import argparse
import datetime
import json
import os
import time
import sqlite3
import secrets
import hashlib
from http.cookies import SimpleCookie
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://mcp.mcd.cn'
LIMIT = 2_000_000

class MCPError(Exception):
    pass

def decode_rpc(raw, content_type, request_id):
    try:
        text = raw.decode('utf-8')
        if 'text/event-stream' in content_type:
            candidates = []
            for event in text.replace('\r\n', '\n').split('\n\n'):
                data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
                if data:
                    candidates.append(json.loads(data))
        else:
            candidates = [json.loads(text)]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get('id') == request_id:
                if 'error' in obj:
                    raise MCPError('官方 MCP 返回协议错误，请检查授权与服务状态。')
                if 'result' not in obj:
                    break
                return obj['result']
    except (ValueError, UnicodeError):
        raise MCPError('官方 MCP 响应无法解析。') from None
    raise MCPError('官方 MCP 未返回匹配的请求结果。')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MCPError('官方端点发生重定向，已停止以保护凭据。')

class MCPClient:
    def __init__(self, token, opener=None):
        self.token = token
        self.session = None
        self.protocol = '2025-03-26'
        self.counter = 0
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open

    def call(self, method, params=None, notify=False):
        self.counter += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            body['id'] = self.counter
        if params is not None:
            body['params'] = params
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': self.protocol}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers=headers, method='POST')
        try:
            with self.opener(req, timeout=15) as response:
                if response.headers.get('Mcp-Session-Id'):
                    self.session = response.headers['Mcp-Session-Id']
                content_type = response.headers.get('Content-Type', '')
                if not notify and 'text/event-stream' in content_type:
                    raw = b''
                    total = 0
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        line = response.readline(LIMIT + 1 - total)
                        total += len(line)
                        if total > LIMIT:
                            raise MCPError('官方响应过大，已停止读取。')
                        raw += line
                        if line in (b'\n', b'\r\n') or not line:
                            try:
                                return decode_rpc(raw, content_type, self.counter)
                            except MCPError as exc:
                                if str(exc) != '官方 MCP 未返回匹配的请求结果。':
                                    raise
                            raw = b''
                        if not line:
                            break
                    raise MCPError('官方 MCP 流未返回匹配结果或已超时。')
                raw = response.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise MCPError('官方响应过大，已停止读取。')
                if notify:
                    return None
                return decode_rpc(raw, response.headers.get('Content-Type', ''), self.counter)
        except urllib.error.HTTPError as exc:
            raise MCPError('官方 MCP HTTP 错误 %s，请检查令牌或稍后重试。' % exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MCPError('连接官方 MCP 失败或超时，请检查网络后重试。') from None

    def calendar(self):
        init = self.call('initialize', {'protocolVersion': self.protocol, 'capabilities': {}, 'clientInfo': {'name': 'mai-hui-lai', 'version': '0.1.0'}})
        if not isinstance(init, dict) or not isinstance(init.get('protocolVersion'), str):
            raise MCPError('官方初始化响应缺少协议版本。')
        self.protocol = init['protocolVersion']
        self.call('notifications/initialized', notify=True)
        cursor = None
        selected = None
        for _ in range(10):
            listing = self.call('tools/list', {'cursor': cursor} if cursor else {})
            if not isinstance(listing, dict) or not isinstance(listing.get('tools'), list):
                raise MCPError('官方工具清单格式异常。')
            selected = next((t for t in listing['tools'] if isinstance(t, dict) and t.get('name') == 'campaign-calendar'), None)
            if selected:
                break
            cursor = listing.get('nextCursor')
            if not cursor:
                break
        if not selected:
            raise MCPError('当前官方服务未提供 campaign-calendar 工具。')
        schema = selected.get('inputSchema')
        if not isinstance(schema, dict) or schema.get('type') != 'object':
            raise MCPError('活动工具参数格式已变化，请按最新 tools/list 更新接入。')
        # Empty arguments are safe only for an unambiguous, optional-only schema.
        if schema.get('required') or any(k in schema for k in ('allOf', 'anyOf', 'oneOf', '$ref', 'if', 'dependentRequired')):
            raise MCPError('活动工具需要额外参数；请在 MCP 客户端查看 tools/list 的 inputSchema 后提供，演示不会猜填。')
        result = self.call('tools/call', {'name': 'campaign-calendar', 'arguments': {}})
        if not isinstance(result, dict) or result.get('isError'):
            raise MCPError('官方活动工具执行失败，请在 MCP 客户端检查授权与工具要求。')
        return result

PRODUCT_IDS = {p['id'] for p in json.loads((ROOT / 'data/products.json').read_text())['products']}

@contextmanager
def vote_connection(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE IF NOT EXISTS visitors (id TEXT PRIMARY KEY, imported INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS votes (visitor TEXT NOT NULL REFERENCES visitors(id), product TEXT NOT NULL, PRIMARY KEY(visitor, product))')
    try:
        with db:
            yield db
    finally:
        db.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, value, kind='application/json; charset=utf-8'):
        raw = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, 'new_visitor_cookie', None):
            self.send_header('Set-Cookie', self.new_visitor_cookie)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def boundary(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host')
        origin = self.headers.get('Origin')
        if host not in hosts or (origin and origin not in {'http://' + h for h in hosts}) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send(403, {'error': '仅允许本机同源访问。'})
            return False
        return True

    def vote_api(self, payload=None, importing=False):
        try:
            with vote_connection(getattr(self.server, 'vote_db', ROOT / 'runtime' / 'votes.sqlite3')) as db:
                db.execute('BEGIN IMMEDIATE')
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get('Cookie', ''))
                    token = cookie['mhl_visitor'].value if 'mhl_visitor' in cookie else ''
                except Exception:
                    token = ''
                identity = hashlib.sha256(token.encode()).hexdigest()
                existing = db.execute('SELECT imported FROM visitors WHERE id=?', (identity,)).fetchone() if len(token) == 64 else None
                if existing is None and payload is not None:
                    self.send(409, {'error': '需要浏览器保存当前服务的 Cookie 才能记录心愿。请允许 Cookie 后点击刷新心愿数；旧本机心愿尚未迁移或删除。'})
                    return
                if existing is None:
                    token = secrets.token_hex(32)
                    identity = hashlib.sha256(token.encode()).hexdigest()
                    db.execute('INSERT INTO visitors(id) VALUES (?)', (identity,))
                    existing = (0,)
                    self.new_visitor_cookie = f'mhl_visitor={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000'
                if payload is not None:
                    if importing:
                        if not existing[0]:
                            db.executemany('INSERT OR IGNORE INTO votes(visitor,product) VALUES (?,?)', [(identity, p) for p in payload['productIds']])
                            db.execute('UPDATE visitors SET imported=1 WHERE id=?', (identity,))
                    elif payload['voted']:
                        db.execute('INSERT OR IGNORE INTO votes(visitor,product) VALUES (?,?)', (identity, payload['productId']))
                    else:
                        db.execute('DELETE FROM votes WHERE visitor=? AND product=?', (identity, payload['productId']))
                counts = dict(db.execute('SELECT product,COUNT(*) FROM votes GROUP BY product').fetchall())
                own = [r[0] for r in db.execute('SELECT product FROM votes WHERE visitor=?', (identity,)).fetchall()]
                result = {'counts': {p: counts.get(p, 0) for p in PRODUCT_IDS}, 'votes': own, 'scope': '当前服务累计，按匿名浏览器计数，不是核实人数'}
            self.send(200, result)
        except (sqlite3.Error, OSError):
            self.send(503, {'error': '心愿记录暂时无法读取或保存，请检查本地服务的数据目录权限后重试；原记录保留。'})

    def read_vote_payload(self, importing):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or length <= 0 or length > 8192 or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise ValueError()
            self.connection.settimeout(10)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError()
            if importing:
                if set(payload) != {'productIds'} or not isinstance(payload['productIds'], list) or len(payload['productIds']) > len(PRODUCT_IDS) or any(not isinstance(p, str) or p not in PRODUCT_IDS for p in payload['productIds']):
                    raise ValueError()
            elif set(payload) != {'productId', 'voted'} or not isinstance(payload['productId'], str) or payload['productId'] not in PRODUCT_IDS or type(payload['voted']) is not bool:
                raise ValueError()
            return payload
        except (ValueError, TypeError, OSError):
            self.send(400, {'error': '心愿请求格式不正确，请刷新页面后重试。'})
            return None

    def do_GET(self):
        if not self.boundary():
            return
        if self.path == '/api/votes':
            self.vote_api()
            return
        files = {'/': ('static/index.html', 'text/html; charset=utf-8'), '/app.js': ('static/app.js', 'text/javascript; charset=utf-8'), '/style.css': ('static/style.css', 'text/css; charset=utf-8'), '/data/products.json': ('data/products.json', 'application/json; charset=utf-8')}
        if self.path not in files:
            self.send(404, {'error': '未找到页面。'})
            return
        path, mime = files[self.path]
        self.send(200, (ROOT / path).read_bytes(), mime)

    def do_POST(self):
        if not self.boundary():
            return
        if self.path in ('/api/votes', '/api/votes/import'):
            importing = self.path.endswith('/import')
            payload = self.read_vote_payload(importing)
            if payload is not None:
                self.vote_api(payload, importing)
            return
        if self.path != '/api/calendar':
            self.send(404, {'error': '未找到接口。'})
            return
        # This endpoint accepts no browser-provided parameters or credentials.
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
            self.send(400, {'error': '此接口不接受请求正文。'})
            return
        token = os.environ.get('MCD_MCP_TOKEN', '').strip()
        if not token:
            self.send(503, {'error': '尚未配置 MCD_MCP_TOKEN。请在启动服务的终端设置环境变量并重启；不要在页面填写令牌。'})
            return
        try:
            result = MCPClient(token).calendar()
            # Defense in depth: upstream echo must never expose the secret.
            safe = json.loads(json.dumps(result, ensure_ascii=False).replace(token, '[REDACTED]'))
            self.send(200, {'queriedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'scope': '官方当月营销活动；不是历史菜单或全国停售证明', 'result': safe})
        except MCPError as exc:
            self.send(502, {'error': str(exc)})
        except Exception:
            self.send(502, {'error': '官方返回了未支持的响应，查询未完成。'})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'麦回来 · http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == '__main__':
    main()
