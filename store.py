"""Postgres storage for the main workspace, enabled by DATABASE_URL.

Loads and saves the same state dict as the JSON store, so engine and workspace logic are unchanged.
Credentials stay in the local encrypted vault; only opaque vault references are stored here.
A request holds a session-level advisory lock, so writers are serialized across server processes.
The isolated presenter demo always stays in local JSON.
"""
import contextlib, datetime as dt, json, threading
from decimal import Decimal
from pathlib import Path
from engine import main_seed
from governor.records import BATCH_WINDOW,DECISION_WINDOW,BREAKER_EVENT_WINDOW,REQUEST_WINDOW

MIGRATIONS=Path(__file__).parent/'migrations'
LOCK_KEY=0x476f7654726164  # "GovTrad"
EVENT_LIMIT=500  # Older events stay in the database; only recent ones are loaded into state.
SAFETY_COLUMNS={'dailyLossCap':'daily_loss_cap','marketCap':'market_cap','exposureCap':'exposure_cap','slippage':'slippage_max'}
TOP_FIELDS={'accounts','agentConfigs','permissions','safety','orders','events','keys','chats','schemaVersion','governorBatches','governorDecisions','governorForecasts','governorBreakerEvents','governorReplacedForecasts','governorRequests'}
DECISION_ROW_FIELDS={'batchId','accountId','decidedAt'}  # columns of governor_decisions; the rest is payload
BREAKER_EVENT_ROW_FIELDS={'id','accountId'}  # columns of governor_breaker_events; the rest is payload
ACCOUNT_FIELDS={'id','name','venue','environment','subaccount','liveEnabled','credentialRef','credentialMasks','status','stale','error','ceiling','baseline','balance'}
AGENT_FIELDS={'id','accountId','name','provider','model','modelValidated','modelCredentialRef','modelKeyMask','status','personality','mandate'}
ORDER_FIELDS={'id','agent','accountId','market','direction','amount','request','status','decision','committed','filled','automatic','venueOrderId','quantity','actualCost','time','evaluations'}

ACCOUNT_UPSERT='''INSERT INTO accounts (id,name,venue,environment,subaccount,live_enabled,credential_ref,credential_masks,status,stale,error,ceiling_daily_cap,baseline_day,baseline_equity,balance,extra,created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())
ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,venue=EXCLUDED.venue,environment=EXCLUDED.environment,subaccount=EXCLUDED.subaccount,live_enabled=EXCLUDED.live_enabled,
credential_ref=EXCLUDED.credential_ref,credential_masks=EXCLUDED.credential_masks,status=EXCLUDED.status,stale=EXCLUDED.stale,error=EXCLUDED.error,ceiling_daily_cap=EXCLUDED.ceiling_daily_cap,
baseline_day=EXCLUDED.baseline_day,baseline_equity=EXCLUDED.baseline_equity,balance=EXCLUDED.balance,extra=EXCLUDED.extra'''
AGENT_UPSERT='''INSERT INTO agents (id,account_id,name,provider,model,model_validated,model_credential_ref,model_key_mask,status,personality,auto_limit,daily_cap,minimum_reserve,new_market_approval,extra,created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())
ON CONFLICT (id) DO UPDATE SET account_id=EXCLUDED.account_id,name=EXCLUDED.name,provider=EXCLUDED.provider,model=EXCLUDED.model,model_validated=EXCLUDED.model_validated,
model_credential_ref=EXCLUDED.model_credential_ref,model_key_mask=EXCLUDED.model_key_mask,status=EXCLUDED.status,personality=EXCLUDED.personality,auto_limit=EXCLUDED.auto_limit,
daily_cap=EXCLUDED.daily_cap,minimum_reserve=EXCLUDED.minimum_reserve,new_market_approval=EXCLUDED.new_market_approval,extra=EXCLUDED.extra'''
ORDER_UPSERT='''INSERT INTO orders (id,agent_id,account_id,market_id,market,direction,amount,reasoning,status,decision,committed,filled,automatic,venue_order_id,quantity,actual_cost,extra,created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (id) DO UPDATE SET market_id=EXCLUDED.market_id,market=EXCLUDED.market,amount=EXCLUDED.amount,reasoning=EXCLUDED.reasoning,status=EXCLUDED.status,decision=EXCLUDED.decision,
committed=EXCLUDED.committed,filled=EXCLUDED.filled,automatic=EXCLUDED.automatic,venue_order_id=EXCLUDED.venue_order_id,quantity=EXCLUDED.quantity,actual_cost=EXCLUDED.actual_cost,extra=EXCLUDED.extra'''

REQUEST_UPSERT='''INSERT INTO governor_requests (governor_proposal_id,agent_id,account_id,proposal_id,status,received_at,payload)
VALUES (%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (governor_proposal_id) DO UPDATE SET status=EXCLUDED.status,payload=EXCLUDED.payload'''
FORECAST_UPSERT='''INSERT INTO governor_forecasts (proposal_id,agent_id,market_id,submitted_at,estimated_yes_probability,outcome,resolved_at)
VALUES (%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (proposal_id) DO UPDATE SET outcome=EXCLUDED.outcome,resolved_at=EXCLUDED.resolved_at'''

def signature(value):return json.dumps(value,sort_keys=True,default=str)
def iso(value):return value.astimezone(dt.timezone.utc).isoformat() if isinstance(value,dt.datetime) else value
def present(row,mapping):return {key:row[column] for key,column in mapping.items() if row[column] is not None}

class PostgresStore:
    def __init__(self,url,search_path=None):
        self.url=url;self.search_path=search_path;self.local=threading.local()
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.numeric import FloatLoader
        from psycopg.types.string import TextLoader
        options='-c TimeZone=UTC'+(f' -c search_path={self.search_path}' if self.search_path else '')
        conn=psycopg.connect(self.url,autocommit=True,row_factory=dict_row,options=options,connect_timeout=15)
        # The engine does float arithmetic and compares ids as strings.
        conn.adapters.register_loader('numeric',FloatLoader);conn.adapters.register_loader('uuid',TextLoader)
        return conn
    @contextlib.contextmanager
    def session(self):
        """Hold one connection and the workspace lock for a whole request (load, persist, save)."""
        conn=self.connect();conn.execute('SELECT pg_advisory_lock(%s)',(LOCK_KEY,));self.local.conn=conn;self.local.seen={}
        try:yield
        finally:self.local.conn=None;conn.close()  # Closing the session releases the advisory lock.
    @contextlib.contextmanager
    def connection(self):
        conn=getattr(self.local,'conn',None)
        if conn:yield conn;return
        with self.connect() as conn:yield conn
    def migrate(self):
        with self.connection() as conn:
            conn.execute('SELECT pg_advisory_lock(%s)',(LOCK_KEY+1,))
            conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version int PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
            done={r['version'] for r in conn.execute('SELECT version FROM schema_migrations')};applied=[]
            for path in sorted(MIGRATIONS.glob('[0-9]*.sql')):
                version=int(path.name.split('_')[0])
                if version not in done:
                    with conn.transaction():conn.execute(path.read_text())
                    applied.append(path.name)
            conn.execute('SELECT pg_advisory_unlock(%s)',(LOCK_KEY+1,))
            return applied

    def load(self):
        s=main_seed();seen={}
        with self.connection() as conn,conn.transaction():
            q=lambda sql:conn.execute(sql).fetchall()
            settings=q('SELECT * FROM workspace_settings')[0]
            s.update(settings['extra']);s['schemaVersion']=settings['schema_version']
            s['permissions']={'dailyCap':settings['daily_cap'],'automation':settings['automation']}
            s['safety']={key:settings[column] for key,column in SAFETY_COLUMNS.items()}
            for r in q('SELECT * FROM accounts ORDER BY created_at,id'):
                a={**r['extra'],**present(r,{'id':'id','name':'name','venue':'venue','environment':'environment','subaccount':'subaccount','liveEnabled':'live_enabled','credentialRef':'credential_ref','credentialMasks':'credential_masks','status':'status','stale':'stale','balance':'balance'}),'error':r['error']}
                a['ceiling']={} if r['ceiling_daily_cap'] is None else {'dailyCap':r['ceiling_daily_cap']}
                if r['baseline_day']:a['baseline']={'day':r['baseline_day'].isoformat(),'equity':r['baseline_equity']}
                s['accounts'].append(a)
            for r in q('SELECT * FROM agents ORDER BY created_at,id'):
                g={**r['extra'],**present(r,{'id':'id','accountId':'account_id','name':'name','provider':'provider','model':'model','modelValidated':'model_validated','modelCredentialRef':'model_credential_ref','modelKeyMask':'model_key_mask','status':'status'}),'personality':r['personality']}
                g['mandate']=None if r['auto_limit'] is None else {'autoLimit':r['auto_limit'],'dailyCap':r['daily_cap'],'minimum':r['minimum_reserve'],'newMarketApproval':r['new_market_approval']}
                s['agentConfigs'].append(g);s['chats'][g['id']]=[]
            evaluations={}
            for r in q('SELECT * FROM order_evaluations ORDER BY order_id,seq'):
                evaluations.setdefault(r['order_id'],[]).append({'time':iso(r['evaluated_at']),'outcome':r['outcome'],'checks':r['checks'],'layers':r['layers']})
            for r in q('SELECT * FROM orders ORDER BY created_at DESC,id DESC'):
                o={**r['extra'],**present(r,{'id':'id','agent':'agent_id','accountId':'account_id','market':'market','direction':'direction','amount':'amount','request':'reasoning','status':'status','decision':'decision','committed':'committed','filled':'filled','automatic':'automatic','venueOrderId':'venue_order_id','quantity':'quantity','actualCost':'actual_cost'})}
                o['time']=iso(r['created_at']);o['evaluations']=evaluations.get(o['id'],[]);seen[('evaluations',o['id'])]=len(o['evaluations'])
                s['orders'].append(o)
            s['keys']={f"{r['agent_id']}:{r['key']}":r['order_id'] for r in q('SELECT * FROM idempotency_keys')}
            for r in q('SELECT agent_id,role,text FROM chat_messages ORDER BY agent_id,seq'):
                s['chats'].setdefault(r['agent_id'],[]).append({'role':r['role'],'text':r['text']})
            s['events']=[{'id':r['id'],'time':iso(r['time']),'text':r['text'],'kind':r['kind'],'trade':r['order_id']} for r in conn.execute('SELECT * FROM events ORDER BY time DESC,seq DESC LIMIT %s',(EVENT_LIMIT,))]
            # Governor records: recent windows only (all rows stay in the database); numerics as exact text.
            # COLLATE "C" orders ids by code point, matching governor.records, so reloads keep list order.
            s['governorBatches']=[{'id':r['id'],'accountId':r['account_id'],'startedAt':iso(r['started_at']),'policyVersion':r['policy_version'],'inputs':r['inputs']}
                for r in conn.execute('SELECT * FROM governor_batches ORDER BY started_at DESC,id COLLATE "C" DESC LIMIT %s',(BATCH_WINDOW,))]
            s['governorDecisions']=[{**r['payload'],'batchId':r['batch_id'],'accountId':r['account_id'],'decidedAt':iso(r['decided_at'])}
                for r in conn.execute('SELECT payload,batch_id,account_id,decided_at FROM governor_decisions ORDER BY decided_at DESC,proposal_id COLLATE "C" DESC LIMIT %s',(DECISION_WINDOW,))]
            s['governorForecasts']=[{'proposalId':r['proposal_id'],'agentId':r['agent_id'],'marketId':r['market_id'],'submittedAt':iso(r['submitted_at']),'estimatedYesProbability':r['p'],'outcome':r['o'],'resolvedAt':iso(r['resolved_at'])}
                for r in q('SELECT proposal_id,agent_id,market_id,submitted_at,estimated_yes_probability::text AS p,outcome::text AS o,resolved_at FROM governor_forecasts ORDER BY submitted_at,proposal_id COLLATE "C"')]
            s['governorRequests']=[r['payload'] for r in conn.execute('''SELECT payload FROM governor_requests WHERE status IN ('queued','approved') OR governor_proposal_id IN
                (SELECT governor_proposal_id FROM governor_requests WHERE status NOT IN ('queued','approved') ORDER BY received_at DESC,governor_proposal_id COLLATE "C" DESC LIMIT %s)
                ORDER BY received_at DESC,governor_proposal_id COLLATE "C" DESC''',(REQUEST_WINDOW,))]
            s['governorBreakerEvents']=[{**r['payload'],'id':r['id'],'accountId':r['account_id']}
                for r in conn.execute('SELECT id,account_id,payload FROM governor_breaker_events ORDER BY at DESC,id COLLATE "C" DESC LIMIT %s',(BREAKER_EVENT_WINDOW,))]
        seen.update(self.signatures(s));seen['loaded']=True;self.local.seen=seen
        return s

    def governor_work_pending(self):
        """Cheap idle check for the governor loop: is any governed request queued or awaiting execution?"""
        with self.connection() as conn:
            return conn.execute("SELECT EXISTS(SELECT 1 FROM governor_requests WHERE status IN ('queued','approved')) AS pending").fetchone()['pending']
    def find_governor_request(self,agent_id,proposal_id):
        """An older governed request by the worker's (agent, proposalId), for retries beyond the loaded window."""
        with self.connection() as conn:
            row=conn.execute('SELECT payload FROM governor_requests WHERE agent_id=%s AND proposal_id=%s',(agent_id,proposal_id)).fetchone()
        return row and row['payload']
    def signatures(self,s):
        seen={'settings':signature(self.settings(s)),'events':{e['id'] for e in s['events']},'keys':set(s['keys']),
              'governorBatches':{b['id'] for b in s.get('governorBatches',[])},'governorDecisions':{d['proposalId'] for d in s.get('governorDecisions',[])},
              'governorBreakerEvents':{e['id'] for e in s.get('governorBreakerEvents',[])}}
        seen.update({('forecast',f['proposalId']):signature(f) for f in s.get('governorForecasts',[])})
        seen.update({('request',r['governorProposalId']):signature(r) for r in s.get('governorRequests',[])})
        seen.update({('chat',agent):len(messages) for agent,messages in s['chats'].items()})
        for kind,rows in (('account',s['accounts']),('agent',s['agentConfigs']),('order',s['orders'])):
            seen.update({(kind,x['id']):signature(x) for x in rows})
        return seen
    def settings(self,s):
        return {'permissions':s['permissions'],'safety':s.get('safety',{}),'schemaVersion':s.get('schemaVersion',3),'extra':{k:v for k,v in s.items() if k not in TOP_FIELDS}}

    def save(self,s):
        """Write rows changed since this request's load; append-only rows are written exactly once."""
        from psycopg.types.json import Jsonb
        j=lambda v:None if v is None else Jsonb(v)
        seen=getattr(self.local,'seen',None) or {};changed=lambda kind,x:seen.get((kind,x['id']))!=signature(x)
        with self.connection() as conn,conn.transaction():
            cur=conn.cursor();run=lambda sql,rows:rows and cur.executemany(sql,rows)
            # Without a load every governor row looks new. Saving fresh rows is fine, but one that already exists
            # would collide (governor inserts have no ON CONFLICT), so refuse with a clear message first.
            # Forecasts upsert and cannot collide. Runs in this transaction, under the workspace lock.
            ids=([b['id'] for b in s.get('governorBatches',[])],[d['proposalId'] for d in s.get('governorDecisions',[])],
                 [e['id'] for e in s.get('governorBreakerEvents',[])])
            if not seen.get('loaded') and any(ids) and conn.execute('''SELECT EXISTS(SELECT 1 FROM governor_batches WHERE id=ANY(%s::text[]))
                OR EXISTS(SELECT 1 FROM governor_decisions WHERE proposal_id=ANY(%s::text[]))
                OR EXISTS(SELECT 1 FROM governor_breaker_events WHERE id=ANY(%s::text[])) AS clash''',ids).fetchone()['clash']:
                raise ValueError('Load the workspace in this session before saving governor records that already exist.')
            settings=self.settings(s)
            if seen.get('settings')!=signature(settings):
                cur.execute('UPDATE workspace_settings SET daily_cap=%s,automation=%s,daily_loss_cap=%s,market_cap=%s,exposure_cap=%s,slippage_max=%s,schema_version=%s,extra=%s',
                    (s['permissions']['dailyCap'],s['permissions']['automation'],*(settings['safety'][k] for k in SAFETY_COLUMNS),settings['schemaVersion'],Jsonb(settings['extra'])))
            run(ACCOUNT_UPSERT,[(a['id'],a['name'],a['venue'],a['environment'],a.get('subaccount',0),a.get('liveEnabled',False),a.get('credentialRef'),j(a.get('credentialMasks')),a['status'],a.get('stale',True),a.get('error'),
                (a.get('ceiling') or {}).get('dailyCap'),(a.get('baseline') or {}).get('day'),(a.get('baseline') or {}).get('equity'),j(a.get('balance')),Jsonb({k:v for k,v in a.items() if k not in ACCOUNT_FIELDS}))
                for a in s['accounts'] if changed('account',a)])
            run(AGENT_UPSERT,[(g['id'],g['accountId'],g['name'],g['provider'],g['model'],g.get('modelValidated',False),g.get('modelCredentialRef'),g.get('modelKeyMask'),g['status'],g.get('personality'),
                *((g['mandate'][k] for k in ('autoLimit','dailyCap','minimum','newMarketApproval')) if g.get('mandate') else (None,)*4),Jsonb({k:v for k,v in g.items() if k not in AGENT_FIELDS}))
                for g in s['agentConfigs'] if changed('agent',g)])
            run(ORDER_UPSERT,[(o['id'],o['agent'],o['accountId'],o['market']['id'],Jsonb(o['market']),o['direction'],o['amount'],o.get('request'),o['status'],o.get('decision'),o.get('committed'),o.get('filled',False),
                o.get('automatic'),o.get('venueOrderId'),o.get('quantity'),o.get('actualCost'),Jsonb({k:v for k,v in o.items() if k not in ORDER_FIELDS}),o['time'])
                for o in s['orders'] if changed('order',o)])
            run('INSERT INTO order_evaluations (order_id,seq,evaluated_at,outcome,checks,layers) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                [(o['id'],i,e['time'],e['outcome'],Jsonb(e['checks']),Jsonb(e['layers'])) for o in s['orders'] for i,e in enumerate(o['evaluations']) if i>=seen.get(('evaluations',o['id']),0)])
            run('INSERT INTO idempotency_keys (agent_id,key,order_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                [(*scoped.split(':',1),order) for scoped,order in s['keys'].items() if scoped not in seen.get('keys',())])
            run('INSERT INTO chat_messages (agent_id,seq,role,text) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                [(agent,i,m['role'],m['text']) for agent,messages in s['chats'].items() for i,m in enumerate(messages) if i>=seen.get(('chat',agent),0)])
            # State lists events newest first; insert oldest first so seq follows insertion order.
            run('INSERT INTO events (id,time,kind,text,order_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                [(e['id'],e['time'],e['kind'],e['text'],e.get('trade')) for e in reversed(s['events']) if e['id'] not in seen.get('events',())])
            # Governor batches, decisions and breaker events are written once. No ON CONFLICT clause: state only holds
            # a recent window, so reusing an id that left it must fail the save loudly instead of being skipped.
            run('INSERT INTO governor_batches (id,started_at,account_id,policy_version,inputs) VALUES (%s,%s,%s,%s,%s)',
                [(b['id'],b['startedAt'],b['accountId'],b['policyVersion'],Jsonb(b['inputs'])) for b in reversed(s.get('governorBatches',[])) if b['id'] not in seen.get('governorBatches',())])
            run('''INSERT INTO governor_decisions (proposal_id,batch_id,account_id,agent_id,market_id,side,decision,approved_budget,contracts,reason_codes,policy_version,decided_at,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                [(d['proposalId'],d['batchId'],d['accountId'],d['agentId'],d['marketId'],d['side'],d['decision'],Decimal(d['approvedBudgetUsd']),Decimal(d['contracts']),
                  list(d['reasonCodes']),d['policyVersion'],d['decidedAt'],Jsonb({k:v for k,v in d.items() if k not in DECISION_ROW_FIELDS}))
                 for d in reversed(s.get('governorDecisions',[])) if d['proposalId'] not in seen.get('governorDecisions',())])
            # Delete only forecasts record_forecast replaced, never rows that are merely outside a loaded window.
            replaced=sorted(set(s.get('governorReplacedForecasts',[])))
            if replaced:cur.execute('DELETE FROM governor_forecasts WHERE proposal_id = ANY(%s)',(replaced,))
            run(FORECAST_UPSERT,[(f['proposalId'],f['agentId'],f['marketId'],f['submittedAt'],Decimal(f['estimatedYesProbability']),
                None if f['outcome'] is None else Decimal(f['outcome']),f['resolvedAt'])
                for f in s.get('governorForecasts',[]) if seen.get(('forecast',f['proposalId']))!=signature(f)])
            # Requests change status over time, so they upsert when changed (never collide on a repeat save).
            run(REQUEST_UPSERT,[(r['governorProposalId'],r['agentId'],r['accountId'],r['proposalId'],r['status'],r['receivedAt'],Jsonb(r))
                for r in reversed(s.get('governorRequests',[])) if seen.get(('request',r['governorProposalId']))!=signature(r)])
            run('INSERT INTO governor_breaker_events (id,at,account_id,action,payload) VALUES (%s,%s,%s,%s,%s)',
                [(e['id'],e['at'],e['accountId'],e['action'],Jsonb({k:v for k,v in e.items() if k not in BREAKER_EVENT_ROW_FIELDS}))
                 for e in reversed(s.get('governorBreakerEvents',[])) if e['id'] not in seen.get('governorBreakerEvents',())])
        seen=self.signatures(s)
        seen.update({('evaluations',o['id']):len(o['evaluations']) for o in s['orders']})
        seen['loaded']=True;self.local.seen=seen
        s['governorReplacedForecasts']=[]  # deleted above; nothing left to replace

if __name__=='__main__':
    import os,sys
    if sys.argv[1:]!=['migrate'] or not os.environ.get('DATABASE_URL'):sys.exit('Usage: DATABASE_URL=... python store.py migrate')
    applied=PostgresStore(os.environ['DATABASE_URL']).migrate();print('Applied: '+', '.join(applied) if applied else 'Database schema is up to date.')
