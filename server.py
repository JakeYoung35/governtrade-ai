import json, os, threading
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from engine import seed, action, AGENTS
ROOT=Path(__file__).parent; DATA=ROOT/'.data';DATA.mkdir(exist_ok=True);LOCK=threading.RLock()
def read(env):
 p=DATA/(env+'.json');return json.loads(p.read_text()) if p.exists() else seed()
def save(env,s):
 p=DATA/(env+'.json');tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(s));tmp.replace(p)
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'static'),**kw)
 def json(self,code,p):
  raw=json.dumps(p).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',len(raw));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path.startswith('/api/state'):
   env='demo' if 'demo' in self.path else 'main'
   with LOCK:self.json(200,{'state':read(env),'agents':AGENTS})
  else:super().do_GET()
 def do_POST(self):
  if self.path!='/api/action':return self.json(404,{'error':'Not found'})
  try:
   b=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))));env='demo' if b.get('env')=='demo' else 'main'
   with LOCK:
    s=read(env)
    if b['action']=='reset' and env=='demo':s=seed();result=None
    else:result=action(s,b)
    save(env,s);self.json(200,{'state':s,'result':result,'agents':AGENTS})
  except (ValueError,KeyError,StopIteration,TypeError) as e:self.json(400,{'error':str(e) or 'Invalid request.'})
if __name__=='__main__':
 port=int(os.environ.get('PORT','3110'));print(f'GovernTrade AI: http://localhost:{port}',flush=True);ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
