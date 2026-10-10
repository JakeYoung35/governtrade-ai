import contextlib,json,os,secrets,threading,hmac
from pathlib import Path
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from engine import seed,main_seed,action,AGENTS,migrate
from workspace import Workspace
from vault import Vault
from store import PostgresStore
from governor.records import compact_for_json
ROOT=Path(__file__).parent;DATA=ROOT/'.data';DATA.mkdir(exist_ok=True);LOCK=threading.RLock();SESSION=secrets.token_urlsafe(32);CSRF=secrets.token_urlsafe(32)
# DATABASE_URL moves the main workspace to Postgres; the presenter demo always stays in local JSON.
STORE=PostgresStore(os.environ['DATABASE_URL']) if os.environ.get('DATABASE_URL') else None
@contextlib.contextmanager
def locked(env):
    with LOCK,(STORE.session() if STORE and env=='main' else contextlib.nullcontext()):yield
def read(env):
    if env=='main' and STORE:return migrate(STORE.load())
    p=DATA/(env+'.json');s=json.loads(p.read_text()) if p.exists() else (seed() if env=='demo' else main_seed())
    if env=='main' and s.get('schemaVersion') not in (2,3):
        if p.exists() and not (DATA/'legacy-simulation.json').exists():p.rename(DATA/'legacy-simulation.json')
        s=main_seed()
    return migrate(s) if env=='main' else (s if s.get('demoAgents') else seed())
def save(env,s):
    if env=='main' and STORE:return STORE.save(s)
    if env=='main':s=compact_for_json(s)  # no database in JSON mode, so bound the governor history kept on disk
    p=DATA/(env+'.json');tmp=p.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(s,f,allow_nan=False)
    tmp.replace(p)
WORKSPACE=Workspace(Vault(DATA),lambda s:save('main',s))
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'static'),**kw)
    def valid_host(self):
        host=self.headers.get('Host','').split(':')[0];return host in ('localhost','127.0.0.1')
    def json(self,code,p):
        raw=json.dumps(p,allow_nan=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Set-Cookie',f'governtrade={SESSION}; HttpOnly; SameSite=Strict; Path=/');self.send_header('Content-Length',len(raw));self.end_headers();self.wfile.write(raw)
    def user_access(self):
        origin=self.headers.get('Origin');return (not origin or origin==f"http://{self.headers.get('Host')}") and f'governtrade={SESSION}' in self.headers.get('Cookie','') and hmac.compare_digest(self.headers.get('X-CSRF-Token',''),CSRF)
    def response(self,env,s,result=None):return {'state':WORKSPACE.public(s) if env=='main' else s,'agents':AGENTS,'result':result,'csrf':CSRF}
    def do_GET(self):
        if not self.valid_host():return self.json(403,{'error':'Localhost access only.'})
        if self.path.startswith('/api/worker'):
            token=os.environ.get('GOVERNTRADE_AGENT_TOKEN','')
            if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.json(403,{'error':'Worker access denied.'})
            s=read('main');return self.json(200,{'agents':WORKSPACE.public(s)['agentConfigs'],'instructions':s['chats']})
        if self.path.startswith('/api/state'):
            if self.headers.get('Origin') and self.headers['Origin']!=f"http://{self.headers.get('Host')}":return self.json(403,{'error':'Cross-origin access denied.'})
            env='demo' if 'demo' in self.path else 'main'
            with locked(env):
                s=read(env)
                if env=='main':WORKSPACE.action(s,{'action':'refresh'});save(env,s)
                self.json(200,self.response(env,s))
        else:super().do_GET()
    def do_POST(self):
        if not self.valid_host():return self.json(403,{'error':'Localhost access only.'})
        proposal=self.path=='/api/agent/proposals'
        token=os.environ.get('GOVERNTRADE_AGENT_TOKEN','')
        if proposal:
            if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.json(403,{'error':'Worker access denied.'})
        elif self.path!='/api/action':return self.json(404,{'error':'Not found.'})
        elif not self.user_access():return self.json(403,{'error':'Reload the local workspace before making changes.'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=100000:raise ValueError('Request too large or empty.')
            b=json.loads(self.rfile.read(size));env='main' if proposal else 'demo' if b.get('env')=='demo' else 'main'
            with locked(env):
                s=read(env)
                if proposal:result=WORKSPACE.proposal(s,b)
                elif env=='main':result=WORKSPACE.action(s,b)
                elif b['action']=='reset':s=seed();result=None
                else:result=action(s,b)
                save(env,s)
                if proposal:self.json(200,{'order':result})
                else:self.json(200,self.response(env,s,result))
        except ValueError as e:self.json(400,{'error':str(e) if not isinstance(e,json.JSONDecodeError) else 'Invalid JSON.'})
        except (KeyError,StopIteration,TypeError,ImportError):self.json(400,{'error':'Invalid configuration or request. Check required fields and installed dependencies.'})
        except Exception:self.json(500,{'error':'Operation failed. Credentials and provider responses are not included in errors.'})
if __name__=='__main__':
    if STORE:applied=STORE.migrate();print('Postgres storage: '+(', '.join(applied)+' applied.' if applied else 'schema up to date.'),flush=True)
    port=int(os.environ.get('PORT','3110'));print(f'GovernTrade AI: http://localhost:{port}',flush=True);ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
