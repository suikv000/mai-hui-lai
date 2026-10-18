import io
import json
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import MCPClient, MCPError, decode_rpc, Handler, ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]

class Response(io.BytesIO):
    def __init__(self, raw, headers=None):
        super().__init__(raw)
        self.headers=headers or {'Content-Type':'application/json'}

class ProtocolTests(unittest.TestCase):
    def test_json_and_sse_match_ids(self):
        self.assertEqual(decode_rpc(b'{"id":1,"result":{"ok":true}}','application/json',1),{'ok':True})
        self.assertEqual(decode_rpc(b'data: {"method":"progress"}\r\n\r\ndata: {"id":2,"result":{}}\r\n\r\n','text/event-stream',2),{})
        for body in (b'bad',b'{"id":7,"result":{}}',b'{"id":1,"error":{"message":"SECRET"}}'):
            with self.assertRaises(MCPError) as e: decode_rpc(body,'application/json',1)
            self.assertNotIn('SECRET',str(e.exception))
    def fake(self, required=False, tool_error=False, sse=False):
        calls=[]
        def open_(req, timeout):
            x=json.loads(req.data);calls.append((x,dict(req.header_items())))
            if x['method']=='notifications/initialized':return Response(b'')
            if x['method']=='initialize':result={'protocolVersion':'2025-03-26'}
            elif x['method']=='tools/list':result={'tools':[{'name':'campaign-calendar','inputSchema':{'type':'object','required':['city'] if required else []}}]}
            else:result={'content':[{'type':'text','text':'example'}],'isError':tool_error}
            raw=json.dumps({'jsonrpc':'2.0','id':x['id'],'result':result}).encode()
            return Response(b'data: '+raw+b'\n\n' if sse else raw,{'Content-Type':'text/event-stream' if sse else 'application/json','Mcp-Session-Id':'test-session'})
        return calls,open_
    def test_workflow_session_and_no_browser_params(self):
        calls,opener=self.fake(sse=True)
        self.assertEqual(MCPClient('secret',opener).calendar()['content'][0]['text'],'example')
        self.assertEqual([x[0]['method'] for x in calls],['initialize','notifications/initialized','tools/list','tools/call'])
        self.assertEqual(calls[-1][0]['params'],{'name':'campaign-calendar','arguments':{}})
        self.assertEqual(calls[-1][1]['Mcp-session-id'],'test-session')
    def test_required_params_stop(self):
        calls,opener=self.fake(required=True)
        with self.assertRaises(MCPError):MCPClient('secret',opener).calendar()
        self.assertNotIn('tools/call',[x[0]['method'] for x in calls])
    def test_tool_error(self):
        _,opener=self.fake(tool_error=True)
        with self.assertRaises(MCPError):MCPClient('secret',opener).calendar()
    def test_timeout_no_leak(self):
        def fail(*a,**k):raise urllib.error.URLError('secret')
        with self.assertRaises(MCPError) as e:MCPClient('secret',fail).calendar()
        self.assertNotIn('secret',str(e.exception))

class DataTests(unittest.TestCase):
    def test_data_preserved_and_refs_resolve(self):
        data=json.loads((ROOT/'data/products.json').read_text())
        self.assertEqual(len(data['products']),38)
        self.assertEqual(len(set(p['id'] for p in data['products'])),38)
        self.assertEqual((ROOT/'data/products.json').read_bytes(),(ROOT/'skills/mai-hui-lai/references/products.json').read_bytes())
        source={s['id'] for s in data['sources']}
        import re
        for p in data['products']:
            self.assertTrue(p['status'])
            refs=re.findall(r'S\d+',p['sourceRefs'])
            self.assertTrue(set(refs)<=source)
            if p['id']!='MHL001': self.assertTrue(refs)
        first=data['products'][0]
        self.assertIn('冰橘茶',first['notes'])
        self.assertIn('尚未',first['status'])
        self.assertEqual(sum('回归公告' in p['status'] for p in data['products']),2)

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,method='GET',headers=None,data=None):
        try:r=urllib.request.urlopen(urllib.request.Request(self.base+path,method=method,headers=headers or {},data=data))
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,r.read(),r.headers
    def test_allowlist_and_headers(self):
        status,body,h=self.request('/')
        self.assertEqual(status,200);self.assertIn(b'<!doctype html>',body);self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
        for p in ('/server.py','/../README.md','/.env','/api/token'):
            self.assertEqual(self.request(p)[0],404)
    def test_origin_and_host(self):
        self.assertEqual(self.request('/',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/calendar','POST',{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
    def test_missing_token_and_body_rejected(self):
        with patch.dict('os.environ',{},clear=True):
            status,body,_=self.request('/api/calendar','POST')
            self.assertEqual(status,503);self.assertIn('MCD_MCP_TOKEN',body.decode())
        self.assertEqual(self.request('/api/calendar','POST',data=b'{}')[0],400)
    def test_upstream_secret_redacted(self):
        with patch.dict('os.environ',{'MCD_MCP_TOKEN':'VERY_PRIVATE'}),patch('server.MCPClient.calendar',return_value={'content':[{'text':'VERY_PRIVATE'}]}):
            status,body,_=self.request('/api/calendar','POST')
            self.assertEqual(status,200);self.assertNotIn(b'VERY_PRIVATE',body);self.assertIn(b'REDACTED',body)

if __name__=='__main__':unittest.main()
