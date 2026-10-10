import contextlib,json,os,secrets,threading,hmac,time
from pathlib import Path
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from engine import seed,main_seed,action,AGENTS,migrate
from workspace import Workspace
from vault import Vault
from store import PostgresStore
from governor.records import compact_for_json
from governor_service import GovernorService
import hosting
ROOT=Path(__file__).parent;DATA=Path(os.environ.get('GOVERNTRADE_DATA_DIR',str(ROOT/'.data')));DATA.mkdir(parents=True,exist_ok=True);LOCK=threading.RLock();SESSION=secrets.token_urlsafe(32);CSRF=secrets.token_urlsafe(32)
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
GOVERNOR=GovernorService(WORKSPACE,find_request=STORE.find_governor_request if STORE else None)
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'static'),**kw)
    def valid_host(self):
        return hosting.allowed_host(self.headers.get('Host',''))
    def same_origin(self):
        origin=self.headers.get('Origin');return not origin or origin==hosting.expected_origin(self.headers.get('Host',''))
    def entry_access(self):
        if urlsplit(self.path).path=='/healthz' and self.command in ('GET','HEAD'):
            raw=b'{"status":"ok"}';self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers()
            if self.command!='HEAD':self.wfile.write(raw)
            return False
        if not self.valid_host():
            self.json(403,{'error':'Host access denied.'});return False
        if not hosting.hosted():return True
        path=urlsplit(self.path).path
        if path=='/api/agent/proposals':
            self.json(410,{'error':'This route is retired on hosted instances. Submit through /api/governor/proposals.'});return False
        if not os.environ.get('GOVERNTRADE_ACCESS_PASSWORD'):
            self.json(503,{'error':'Hosted workspace is locked. Configure an access password before connecting accounts.'});return False
        worker_path=path in ('/api/worker','/api/governor/proposals','/api/governor/schema') or path.startswith('/api/governor/proposals/')
        if (worker_path and self.worker_ok()) or hosting.password_ok(self.headers.get('Authorization','')):return True
        self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="GovernTrade private workspace", charset="UTF-8"');self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0');self.end_headers();return False
    def do_HEAD(self):
        if self.entry_access():super().do_HEAD()
    def json(self,code,p):
        raw=json.dumps(p,allow_nan=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if code<400:self.send_header('Set-Cookie',f'governtrade={SESSION}; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if hosting.hosted() else ''))
        self.send_header('Content-Length',len(raw));self.end_headers()
        if self.command!='HEAD':self.wfile.write(raw)
    def user_access(self):
        return self.same_origin() and f'governtrade={SESSION}' in self.headers.get('Cookie','') and hmac.compare_digest(self.headers.get('X-CSRF-Token',''),CSRF)
    def worker_ok(self):
        token=os.environ.get('GOVERNTRADE_AGENT_TOKEN','');return bool(token) and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
    def governor_get(self):
        # Governed proposal API (governor/api.py describes the contract). The summary is a same-origin read like /api/state.
        path=urlsplit(self.path).path
        if path=='/api/governor/summary':
            if not self.same_origin():return self.json(403,{'error':'Cross-origin access denied.'})
            with locked('main'):return self.json(200,GOVERNOR.summary(read('main')))
        if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
        if path=='/api/governor/schema':return self.json(200,GOVERNOR.schema())
        if path.startswith('/api/governor/proposals/'):
            with locked('main'):found=GOVERNOR.lookup(read('main'),path.rsplit('/',1)[1])
            return self.json(200,found) if found else self.json(404,{'error':'Unknown governorProposalId.'})
        return self.json(404,{'error':'Not found.'})
    def governor_submit(self):
        if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=100000:raise ValueError('Request too large or empty.')
            payload=json.loads(self.rfile.read(size))
        except ValueError:return self.json(400,{'error':'Invalid JSON.','errors':[{'field':'','error':'body must be one JSON object of at most 100 kB'}],'schema':'/api/governor/schema'})
        try:
            with locked('main'):
                s=read('main');code,body=GOVERNOR.submit(s,payload)
                if code==202:save('main',s)
            self.json(code,body)
        except Exception:self.json(500,{'error':'Operation failed. Credentials and provider responses are not included in errors.'})
    def response(self,env,s,result=None):return {'state':WORKSPACE.public(s) if env=='main' else s,'agents':AGENTS,'result':result,'csrf':CSRF}
    def do_GET(self):
        if not self.entry_access():return
        if self.path.startswith('/api/governor/'):return self.governor_get()
        if self.path.startswith('/api/worker'):
            if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
            s=read('main');return self.json(200,{'agents':WORKSPACE.public(s)['agentConfigs'],'instructions':s['chats']})
        if self.path.startswith('/api/state'):
            if not self.same_origin():return self.json(403,{'error':'Cross-origin access denied.'})
            env='demo' if 'demo' in self.path else 'main'
            with locked(env):
                s=read(env)
                if env=='main':WORKSPACE.action(s,{'action':'refresh'});save(env,s)
                self.json(200,self.response(env,s))
        else:super().do_GET()
    def do_POST(self):
        if not self.entry_access():return
        if self.path=='/api/governor/proposals':return self.governor_submit()
        proposal=self.path=='/api/agent/proposals'
        if proposal:
            if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
        elif self.path!='/api/action':return self.json(404,{'error':'Not found.'})
        elif not self.user_access():return self.json(403,{'error':'Reload the local workspace before making changes.'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=100000:raise ValueError('Request too large or empty.')
            b=json.loads(self.rfile.read(size));env='main' if proposal else 'demo' if b.get('env')=='demo' else 'main'
            with locked(env):
                s=read(env)
                if proposal:result=WORKSPACE.proposal(s,b)
                elif env=='main':result=GOVERNOR.action(s,b) if str(b.get('action','')).startswith('governor-') else WORKSPACE.action(s,b)
                elif b['action']=='reset':s=seed();result=None
                else:result=action(s,b)
                save(env,s)
                if proposal:self.json(200,{'order':result})
                else:self.json(200,self.response(env,s,result))
        except ValueError as e:self.json(400,{'error':str(e) if not isinstance(e,json.JSONDecodeError) else 'Invalid JSON.'})
        except (KeyError,StopIteration,TypeError,ImportError):self.json(400,{'error':'Invalid configuration or request. Check required fields and installed dependencies.'})
        except Exception:self.json(500,{'error':'Operation failed. Credentials and provider responses are not included in errors.'})
SCORE_SECONDS=60  # how often settled markets are checked to score agent forecasts
def governor_pass(score):
    # Plan under the lock, read the venue (balances, order books, market outcomes) with the lock released, then
    # decide, execute and save under the lock. Order placement still runs through Workspace.proposal under the lock.
    with locked('main'):
        s=read('main');work=any(r['status'] in ('queued','approved') for r in s.get('governorRequests',[]))
        plans=GOVERNOR.plan(s);scoring=GOVERNOR.plan_scoring(s) if score else {}
    if not work and not scoring:return False
    fetched=GOVERNOR.fetch(plans);outcomes=GOVERNOR.fetch_outcomes(scoring)
    with locked('main'):
        s=read('main')
        if work:GOVERNOR.run_batch(s,fetched)
        if scoring:GOVERNOR.score_forecasts(s,outcomes)
        save('main',s)
    return True
def governor_loop(seconds):
    # A failed pass leaves everything queued or unscored for the next one.
    last_scored=0.
    while True:
        time.sleep(seconds)
        try:
            score=time.monotonic()-last_scored>=SCORE_SECONDS
            if STORE and not score and not STORE.governor_work_pending():continue  # idle: skip the full workspace load
            governor_pass(score)
            if score:last_scored=time.monotonic()
        except Exception as e:print(f'Governor pass failed ({type(e).__name__}); queued proposals stay queued.',flush=True)
if __name__=='__main__':
    if STORE:applied=STORE.migrate();print('Postgres storage: '+(', '.join(applied)+' applied.' if applied else 'schema up to date.'),flush=True)
    seconds=float(os.environ.get('GOVERNOR_BATCH_SECONDS','5'))
    if seconds>0:threading.Thread(target=governor_loop,args=(seconds,),daemon=True).start()
    port=int(os.environ.get('PORT','3110'));host=hosting.bind_host();print(f'GovernTrade AI listening on {host}:{port}',flush=True);ThreadingHTTPServer((host,port),Handler).serve_forever()
