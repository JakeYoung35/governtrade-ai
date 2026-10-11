import contextlib,json,os,secrets,threading,hmac,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from engine import seed,main_seed,action,AGENTS,migrate
from workspace import Workspace
from vault import Vault
from store import PostgresStore
from governor.records import compact_for_json
from governor_service import GovernorService
from agent_chat import AgentChat,CHAT_PROPOSAL_PREFIX
import hosting
import authentication
import tenancy
ROOT=Path(__file__).parent;DATA=Path(os.environ.get('GOVERNTRADE_DATA_DIR',str(ROOT/'.data')));DATA.mkdir(parents=True,exist_ok=True);LOCK=threading.RLock();SESSION=secrets.token_urlsafe(32);CSRF=secrets.token_urlsafe(32)
# DATABASE_URL moves the main workspace to Postgres; the presenter demo always stays in local JSON.
STORE=PostgresStore(os.environ['DATABASE_URL']) if os.environ.get('DATABASE_URL') else None
def runtime():
    """The signed-in user's tenant (personal workspaces), or None in local single-user mode."""
    return tenancy.CURRENT.get()
# Each falls back to the module-level single-user objects, looked up on every call.
def workspace():return runtime().workspace if runtime() else WORKSPACE
def governor():return runtime().governor if runtime() else GOVERNOR
def chat_service():return runtime().chat if runtime() else AGENT_CHAT
def data_dir():return runtime().directory if runtime() else DATA
def active_store():return None if runtime() else STORE
_AUTH={};_AUTH_LOCK=threading.Lock()
def auth_store():
    # One store per data directory (tests swap DATA). Its own lock: the workspace lock guards workspace state only.
    with _AUTH_LOCK:
        if DATA not in _AUTH:_AUTH[DATA]=authentication.AuthStore(DATA)
        return _AUTH[DATA]
_READY={}
def multiuser_ready():
    if STORE:raise ValueError('Multiuser mode cannot use the unscoped legacy DATABASE_URL storage.')
    if not hosting.hosted():return
    names=('GOVERNTRADE_PERSISTENT_STORAGE','GOVERNTRADE_DATA_DIR','GOVERNTRADE_MASTER_KEY','GOVERNTRADE_ALLOWED_EMAILS','GOVERNTRADE_ALLOW_EPHEMERAL_WORKSPACES',
           'AUTH0_DOMAIN','AUTH0_CLIENT_ID','AUTH0_CLIENT_SECRET')
    config=tuple(os.environ.get(n,'') for n in names)
    if config in _READY:return  # checked once per configuration, not on every request
    persistent,data_dir_set,master,allowed,ephemeral,*auth0=config
    if not data_dir_set or (persistent!='true' and ephemeral!='true'):
        raise ValueError('Multiuser hosting requires configured data storage and an explicit storage mode.')
    from cryptography.fernet import Fernet
    Fernet(master.encode())
    if not allowed.strip():
        raise ValueError('Set GOVERNTRADE_ALLOWED_EMAILS (addresses, @domains, or * for any verified account).')
    if authentication.provider()=='auth0' and not all(auth0):
        raise ValueError('Auth0 needs AUTH0_DOMAIN, AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET.')
    _READY[config]=True
@contextlib.contextmanager
def locked(env):
    tenant=tenancy.CURRENT.get()
    if tenant:
        with tenant.locked():yield
    else:
        with LOCK,(STORE.session() if STORE and env=='main' else contextlib.nullcontext()):yield
def read(env):
    if env=='main' and active_store():return migrate(active_store().load())
    p=data_dir()/(env+'.json');s=json.loads(p.read_text()) if p.exists() else (seed() if env=='demo' else main_seed())
    if env=='main' and s.get('schemaVersion') not in (2,3):
        if p.exists() and not (data_dir()/'legacy-simulation.json').exists():p.rename(data_dir()/'legacy-simulation.json')
        s=main_seed()
    return migrate(s) if env=='main' else (s if s.get('demoAgents') else seed())
def write_json(path,value):
    tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,allow_nan=False)
    tmp.replace(path)
GOVERNOR_STATUS='governor-status.json'  # per-tenant marker: lets the governor loop skip idle workspaces without loading them
def governor_status(s):
    return {'pending':any(r.get('status') in ('queued','approved') for r in s.get('governorRequests',[])),
            'unresolved':any(f.get('outcome') is None for f in s.get('governorForecasts',[]))}
def save(env,s,directory=None):
    """directory: a tenant's own folder (Tenant binds it); otherwise the current context's."""
    if env=='main' and directory is None and active_store():return active_store().save(s)
    folder=directory or data_dir()
    if env=='main':s=compact_for_json(s)  # no database in JSON mode, so bound the governor history kept on disk
    marker=env=='main' and folder!=DATA
    if marker:
        # Order the writes so a crash between them errs toward "has work": a stale marker then costs one extra pass,
        # never a skipped proposal. Before the workspace, record the union of the old and new status; after, the exact.
        status=governor_status(s);previous=read_governor_status(folder)
        write_json(folder/GOVERNOR_STATUS,{k:status[k] or bool(previous.get(k,True)) for k in status})
    write_json(folder/(env+'.json'),s)
    if marker:write_json(folder/GOVERNOR_STATUS,status)
def read_governor_status(folder):
    """The marker's flags, or {} when it is missing or unreadable (callers treat {} as "has work")."""
    try:
        status=json.loads((folder/GOVERNOR_STATUS).read_text())
        return status if isinstance(status,dict) else {}
    except (OSError,ValueError):return {}
def tenant_save(s,directory):save('main',s,directory)
WORKSPACE=Workspace(Vault(DATA),lambda s:save('main',s))
GOVERNOR=GovernorService(WORKSPACE,find_request=STORE.find_governor_request if STORE else None)
AGENT_CHAT=AgentChat(WORKSPACE,GOVERNOR)
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'static'),**kw)
    def end_headers(self):
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Cross-Origin-Opener-Policy','same-origin-allow-popups')
        super().end_headers()
    def handle_one_request(self):
        self.identity=None;self.session_token='';self.auth_cookies=[]
        super().handle_one_request()
    def scoped(self, callback):
        if authentication.enabled() and self.identity:
            with tenancy.activate(DATA,self.identity['id'],tenant_save):return callback()
        return callback()
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
        if authentication.enabled():
            try:multiuser_ready()
            except (ValueError,TypeError):
                self.json(503,{'error':'Personal workspaces are not configured yet.'});return False
            path=urlsplit(self.path).path
            if not path.startswith('/api/'):return True  # the app shell and assets are public; no session lookup
            self.session_token=authentication.cookie_value(self.headers.get('Cookie',''),self.session_cookie_name())
            self.identity=auth_store().session(self.session_token)
            if path in ('/api/auth','/api/auth/login','/api/auth/logout','/api/auth/start','/api/auth/callback'):return True
            if not self.identity:
                self.json(401,{'error':'Sign in to your personal workspace.'});return False
            if not self.same_origin():
                self.json(403,{'error':'Cross-origin access denied.'});return False
            return True
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
        for cookie in self.auth_cookies:self.send_header('Set-Cookie',cookie)
        if not self.auth_cookies and code<400 and not authentication.enabled():self.send_header('Set-Cookie',f'governtrade={SESSION}; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if hosting.https() else ''))
        self.send_header('Content-Length',len(raw));self.end_headers()
        if self.command!='HEAD':self.wfile.write(raw)
    def user_access(self):
        if authentication.enabled():return bool(self.identity) and self.same_origin() and hmac.compare_digest(self.headers.get('X-CSRF-Token',''),self.identity['csrf'])
        return self.same_origin() and f'governtrade={SESSION}' in self.headers.get('Cookie','') and hmac.compare_digest(self.headers.get('X-CSRF-Token',''),CSRF)
    def worker_ok(self):
        if authentication.enabled():return False  # Legacy global worker tokens cannot select a personal workspace.
        token=os.environ.get('GOVERNTRADE_AGENT_TOKEN','');return bool(token) and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
    def session_cookie_name(self):return '__Host-governtrade-session' if hosting.https() else 'governtrade-session'
    def challenge_cookie_name(self,nonce):
        # One cookie per sign-in attempt, so a second tab does not replace the first tab's challenge.
        return ('__Host-' if hosting.https() else '')+'governtrade-login-'+authentication.digest(nonce)[:16]
    def set_auth_cookie(self,name,value,age,same_site='Strict'):
        self.auth_cookies.append(f'{name}={value}; HttpOnly; SameSite={same_site}; Path=/; Max-Age={age}'+('; Secure' if hosting.https() else ''))
    def redirect(self,location):
        self.send_response(303);self.send_header('Location',location);self.send_header('Cache-Control','no-store')
        for cookie in self.auth_cookies:self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length','0');self.end_headers()
    def origin(self):return hosting.expected_origin(self.headers.get('Host',''))
    def auth_config(self):
        mode=authentication.provider()
        if not mode:return self.json(200,{'mode':'local'})
        if not self.same_origin():return self.json(403,{'error':'Cross-origin access denied.'})
        if self.identity:return self.json(200,{'mode':mode,'user':{k:self.identity[k] for k in ('name','email')},'csrf':self.identity['csrf']})
        if mode=='auth0':return self.json(200,{'mode':'auth0','loginUrl':'/api/auth/start','user':None})
        token,nonce=auth_store().challenge()
        self.set_auth_cookie(self.challenge_cookie_name(nonce),token,authentication.CHALLENGE_SECONDS)
        return self.json(200,{'mode':'google','clientId':os.environ['GOVERNTRADE_GOOGLE_CLIENT_ID'],'nonce':nonce,'user':None})
    def auth_get(self):
        """Auth0's two browser steps: /api/auth/start sends the user to Auth0; /api/auth/callback comes back."""
        path=urlsplit(self.path).path
        if authentication.provider()!='auth0':return self.json(404,{'error':'Not found.'})
        callback=self.origin()+'/api/auth/callback'
        if path=='/api/auth/start':
            token,flow=auth_store().auth0_flow()
            # Lax: the cookie must come back on Auth0's top-level redirect to the callback, a cross-site navigation.
            self.set_auth_cookie(self.flow_cookie_name(flow['state']),token,authentication.CHALLENGE_SECONDS,'Lax')
            return self.redirect(authentication.auth0_authorize_url(flow,callback))
        query=dict(parse_qsl(urlsplit(self.path).query))
        state=query.get('state','')
        cookie=self.flow_cookie_name(state) if state else ''
        if cookie:self.set_auth_cookie(cookie,'',0,'Lax')  # one use: clear it whatever happens next
        if query.get('error') or not cookie:
            return self.redirect('/?signin=failed')
        try:
            token,_=auth_store().login_auth0(authentication.cookie_value(self.headers.get('Cookie',''),cookie),state,query.get('code',''),callback)
        except authentication.NotAllowed:return self.redirect('/?signin=not-allowed')
        except Exception as e:
            print(f'Auth0 sign-in failed ({type(e).__name__}).',flush=True)
            return self.redirect('/?signin=failed')
        if self.session_token:auth_store().revoke(self.session_token)
        self.set_auth_cookie(self.session_cookie_name(),token,authentication.SESSION_SECONDS)
        return self.redirect('/')
    def flow_cookie_name(self,state):
        return ('__Host-' if hosting.https() else '')+'governtrade-auth0-'+authentication.digest(state)[:16]
    def auth_post(self):
        if not authentication.enabled():return self.json(404,{'error':'Not found.'})
        # Require Origin for login/logout, in addition to the one-use browser-bound nonce or session CSRF.
        if self.headers.get('Origin')!=hosting.expected_origin(self.headers.get('Host','')):
            return self.json(403,{'error':'Cross-origin access denied.'})
        if self.path=='/api/auth/logout':
            if not self.user_access():return self.json(403,{'error':'Sign in again before signing out.'})
            auth_store().revoke(self.session_token);self.set_auth_cookie(self.session_cookie_name(),'',0)
            body={'signedOut':True}
            if authentication.provider()=='auth0':body['logoutUrl']=authentication.auth0_logout_url(self.origin()+'/')
            return self.json(200,body)
        if self.path!='/api/auth/login' or authentication.provider()!='google':return self.json(404,{'error':'Not found.'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=20000:raise ValueError('Invalid sign-in.')
            b=json.loads(self.rfile.read(size))
            nonce=b.get('nonce') if isinstance(b.get('nonce'),str) else ''
            name=self.challenge_cookie_name(nonce)
            token,identity=auth_store().login(authentication.cookie_value(self.headers.get('Cookie',''),name),nonce,b.get('credential'),os.environ['GOVERNTRADE_GOOGLE_CLIENT_ID'])
            if self.session_token:auth_store().revoke(self.session_token)
            self.set_auth_cookie(name,'',0)
            self.set_auth_cookie(self.session_cookie_name(),token,authentication.SESSION_SECONDS)
            return self.json(200,{'user':{k:identity[k] for k in ('name','email')},'csrf':identity['csrf']})
        except authentication.NotAllowed as e:return self.json(403,{'error':str(e)})
        except Exception as e:
            # The challenge cookie is kept, so a transient Google or network error can be retried without a reload.
            print(f'Google sign-in failed ({type(e).__name__}).',flush=True)
            return self.json(401,{'error':'Sign-in failed. Try again, or reload this page.'})
    def governor_get(self):
        # Governed proposal API (governor/api.py describes the contract). The summary is a same-origin read like /api/state.
        path=urlsplit(self.path).path
        if path=='/api/governor/summary':
            if not self.same_origin():return self.json(403,{'error':'Cross-origin access denied.'})
            with locked('main'):return self.json(200,governor().summary(read('main')))
        if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
        if path=='/api/governor/schema':return self.json(200,governor().schema())
        if path.startswith('/api/governor/proposals/'):
            with locked('main'):found=governor().lookup(read('main'),path.rsplit('/',1)[1])
            return self.json(200,found) if found else self.json(404,{'error':'Unknown governorProposalId.'})
        return self.json(404,{'error':'Not found.'})
    def governor_submit(self):
        if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=100000:raise ValueError('Request too large or empty.')
            payload=json.loads(self.rfile.read(size))
        except ValueError:return self.json(400,{'error':'Invalid JSON.','errors':[{'field':'','error':'body must be one JSON object of at most 100 kB'}],'schema':'/api/governor/schema'})
        if isinstance(payload,dict) and str(payload.get('proposalId','')).startswith(CHAT_PROPOSAL_PREFIX):
            return self.json(400,{'error':'Invalid proposal.','errors':[{'field':'proposalId','error':f'must not start with "{CHAT_PROPOSAL_PREFIX}", which is reserved for the in-app agent chat'}],'schema':'/api/governor/schema'})
        try:
            with locked('main'):
                s=read('main');code,body=governor().submit(s,payload)
                if code==202:save('main',s)
            self.json(code,body)
        except Exception:self.json(500,{'error':'Operation failed. Credentials and provider responses are not included in errors.'})
    def response(self,env,s,result=None):return {'state':{**workspace().public(s),'chatProposals':chat_service().proposal_views(s)} if env=='main' else s,'agents':AGENTS,'result':result,'csrf':self.identity['csrf'] if authentication.enabled() else CSRF}
    def agent_chat(self,b):
        # Three phases like a governor pass (agent_chat.py): the model and market reads run with the lock released.
        with locked('main'):
            s=read('main');job=chat_service().begin(s,b)
            try:save('main',s)
            except Exception:chat_service().release(job['agentId'],job['turn']);raise
        result=chat_service().run(job)
        try:
            with locked('main'):s=read('main');summary=chat_service().finish(s,job,result);save('main',s)
        finally:chat_service().release(job['agentId'],job['turn'])  # covers a failed read or save; a no-op once finish released it
        self.json(200,self.response('main',s,summary))
    def do_GET(self):
        if not self.entry_access():return
        return self.scoped(self.get_content)
    def get_content(self):
        if urlsplit(self.path).path=='/api/auth':return self.auth_config()
        if urlsplit(self.path).path in ('/api/auth/start','/api/auth/callback'):return self.auth_get()
        if self.path.startswith('/api/governor/'):return self.governor_get()
        if self.path=='/api/chat/proposals':
            # Light poll for the chat UI while proposals are being decided: no venue refresh, unlike /api/state.
            if not self.same_origin():return self.json(403,{'error':'Cross-origin access denied.'})
            with locked('main'):return self.json(200,{'chatProposals':chat_service().proposal_views(read('main'))})
        if self.path.startswith('/api/worker'):
            if not self.worker_ok():return self.json(403,{'error':'Worker access denied.'})
            # The in-app agent answers chat messages itself (agent_chat.py), so they are no longer offered to workers
            # as instructions: a worker trading on them would place every trade twice.
            s=read('main');return self.json(200,{'agents':workspace().public(s)['agentConfigs'],'instructions':{},'chatHandledBy':'app'})
        if self.path.startswith('/api/state'):
            if not self.same_origin():return self.json(403,{'error':'Cross-origin access denied.'})
            env='demo' if 'demo' in self.path else 'main'
            with locked(env):
                s=read(env)
                if env=='main':workspace().action(s,{'action':'refresh'});save(env,s)
                self.json(200,self.response(env,s))
        else:super().do_GET()
    def do_POST(self):
        if not self.entry_access():return
        return self.scoped(self.post_content)
    def post_content(self):
        if self.path.startswith('/api/auth/'):return self.auth_post()
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
            if not proposal and env=='main' and b.get('action')=='chat':return self.agent_chat(b)
            with locked(env):
                s=read(env)
                if proposal:result=workspace().proposal(s,b)
                elif env=='main':result=governor().action(s,b) if str(b.get('action','')).startswith('governor-') else workspace().action(s,b)
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
        plans=governor().plan(s);scoring=governor().plan_scoring(s) if score else {}
    if not work and not scoring:return False
    fetched=governor().fetch(plans);outcomes=governor().fetch_outcomes(scoring)
    with locked('main'):
        s=read('main')
        if work:governor().run_batch(s,fetched)
        if scoring:governor().score_forecasts(s,outcomes)
        save('main',s)
    return True
def tenant_has_governor_work(user_id,score):
    """Read the small marker save() keeps beside each workspace instead of loading the workspace itself. A missing or
    unreadable marker (a workspace saved before markers existed) counts as work, so the pass runs and writes one."""
    folder=DATA/'users'/user_id
    status=read_governor_status(folder)
    if not status:return (folder/'main.json').exists()
    return bool(status.get('pending',True) or (score and status.get('unresolved',True)))
def governor_loop(seconds):
    # A failed pass leaves everything queued or unscored for the next one.
    last_scored=0.
    while True:
        time.sleep(seconds)
        try:
            score=time.monotonic()-last_scored>=SCORE_SECONDS
            if authentication.enabled():
                multiuser_ready()
                for user_id in auth_store().users():
                    if not tenant_has_governor_work(user_id,score):continue
                    try:
                        with tenancy.activate(DATA,user_id,tenant_save):governor_pass(score)
                    except Exception as e:print(f'Personal governor pass failed ({type(e).__name__}).',flush=True)
                if score:last_scored=time.monotonic()
                continue
            if STORE and not score and not STORE.governor_work_pending():continue  # idle: skip the full workspace load
            governor_pass(score)
            if score:last_scored=time.monotonic()
        except Exception as e:print(f'Governor pass failed ({type(e).__name__}); queued proposals stay queued.',flush=True)
if __name__=='__main__':
    if authentication.enabled():multiuser_ready()
    if STORE:applied=STORE.migrate();print('Postgres storage: '+(', '.join(applied)+' applied.' if applied else 'schema up to date.'),flush=True)
    seconds=float(os.environ.get('GOVERNOR_BATCH_SECONDS','5'))
    if seconds>0:threading.Thread(target=governor_loop,args=(seconds,),daemon=True).start()
    port=int(os.environ.get('PORT','3110'));host=hosting.bind_host();print(f'GovernTrade AI listening on {host}:{port}',flush=True);ThreadingHTTPServer((host,port),Handler).serve_forever()
