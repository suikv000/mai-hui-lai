import json
import tempfile
import threading
import urllib.request
import urllib.error
import http.cookiejar
import unittest
from pathlib import Path
from server import Handler, ThreadingHTTPServer

class VoteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'votes.sqlite3'
        self.start()
    def start(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.vote_db=self.path
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def tearDown(self):self.stop();self.temp.cleanup()
    def client(self):return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def req(self,c,payload=None,path='/api/votes',headers=None):
        h={'Content-Type':'application/json'};h.update(headers or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=h)
        try:r=c.open(req)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,json.load(r),r.headers
    def test_two_clients_idempotency_remove_restart(self):
        a,b=self.client(),self.client()
        self.req(b)
        code,x,h=self.req(a);self.assertEqual(code,200);self.assertEqual(x['counts']['MHL037'],0)
        self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])
        for _ in range(2):self.req(a,{'productId':'MHL037','voted':True})
        self.assertEqual(self.req(a)[1]['counts']['MHL037'],1)
        self.req(b,{'productId':'MHL037','voted':True})
        self.assertEqual(self.req(a)[1]['counts']['MHL037'],2)
        self.assertEqual(self.req(b)[1]['votes'],['MHL037'])
        self.stop();self.start()
        self.assertEqual(self.req(a)[1]['counts']['MHL037'],2)
        for _ in range(2):self.req(a,{'productId':'MHL037','voted':False})
        self.assertEqual(self.req(a)[1]['counts']['MHL037'],1)
        self.assertEqual(self.req(a)[1]['votes'],[])
        self.assertEqual(self.req(b)[1]['votes'],['MHL037'])
    def test_import_once_union_no_resurrect_after_undo(self):
        c=self.client();self.req(c)
        self.req(c,{'productId':'MHL002','voted':True})
        for _ in range(2):self.req(c,{'productIds':['MHL037','MHL037']},'/api/votes/import')
        x=self.req(c)[1];self.assertEqual(set(x['votes']),{'MHL002','MHL037'});self.assertEqual(x['counts']['MHL037'],1)
        self.req(c,{'productId':'MHL037','voted':False})
        self.req(c,{'productIds':['MHL037']},'/api/votes/import')
        self.assertEqual(self.req(c)[1]['counts']['MHL037'],0)
    def test_invalid_and_cross_origin(self):
        c=self.client()
        for payload in ({'productId':'BAD','voted':True},{'productId':'MHL037','voted':1},{'productId':'MHL037','voted':True,'voter':'forged'}):
            self.assertEqual(self.req(c,payload)[0],400)
        self.assertEqual(self.req(c,{'productIds':['BAD']},'/api/votes/import')[0],400)
        self.assertEqual(self.req(c,{'productId':'MHL037','voted':True},headers={'Origin':'https://foreign.test'})[0],403)
    def test_write_requires_registered_cookie_no_orphan_votes(self):
        for headers in ({}, {'Cookie':'mhl_visitor='+'b'*64}):
            for payload,path in (({'productId':'MHL037','voted':True},'/api/votes'),({'productIds':['MHL037']},'/api/votes/import')):
                c=self.client()
                code,x,h=self.req(c,payload,path,headers)
                self.assertEqual(code,409)
                self.assertIn('Cookie',x['error'])
                self.assertNotIn('Set-Cookie',h)
        c=self.client()
        self.assertEqual(self.req(c)[1]['counts']['MHL037'],0)
        self.assertEqual(self.req(c,{'productIds':['MHL037']},'/api/votes/import')[1]['counts']['MHL037'],1)

    def test_forged_unregistered_cookie_replaced(self):
        c=self.client();status,x,h=self.req(c,headers={'Cookie':'mhl_visitor='+'a'*64})
        self.assertEqual(status,200);self.assertIn('mhl_visitor=',h['Set-Cookie']);self.assertNotIn('a'*64,h['Set-Cookie'])

if __name__=='__main__':unittest.main()
