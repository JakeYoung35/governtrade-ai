"""Postgres store tests. Skipped unless TEST_DATABASE_URL is set.

Each run migrates a throwaway schema and drops it afterwards, so real tables are never touched.
"""
import copy,os,unittest,uuid
from unittest.mock import patch
from engine import main_seed,PERSONALITIES
from workspace import Workspace
from store import PostgresStore
from test_engine import FakeVault,FakeVenue

URL=os.environ.get('TEST_DATABASE_URL')
@unittest.skipUnless(URL,'Set TEST_DATABASE_URL to run Postgres store tests.')
class StoreTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.schema='governtrade_test_'+uuid.uuid4().hex[:8];admin=PostgresStore(URL)
  with admin.connect() as c:c.execute(f'CREATE SCHEMA {cls.schema}')
  cls.store=PostgresStore(URL,search_path=f'{cls.schema},public');cls.store.migrate()
 @classmethod
 def tearDownClass(cls):
  with PostgresStore(URL).connect() as c:c.execute(f'DROP SCHEMA {cls.schema} CASCADE')
 def setUp(self):
  with self.store.connect() as c:c.execute('TRUNCATE governor_requests,governor_breaker_events,governor_decisions,governor_forecasts,governor_batches,events,balance_snapshots,chat_messages,idempotency_keys,order_evaluations,orders,agents,accounts');c.execute("UPDATE workspace_settings SET daily_cap=100,automation=true,extra='{}'")
  self.v=FakeVenue();self.w=Workspace(FakeVault());self.w.venue=lambda a:self.v
 def workspace(self):
  s=main_seed();a={'id':str(uuid.uuid4()),'name':'Kalshi Demo','venue':'kalshi','environment':'sandbox','connectionType':'kalshi-demo','subaccount':0,'liveEnabled':False,'credentialRef':'REF','credentialMasks':{'key_id':'••••1234'},'status':'connected','stale':False,'error':None,'ceiling':{'dailyCap':100}}
  g={'id':str(uuid.uuid4()),'name':'Agent','accountId':a['id'],'provider':'openai','model':'gpt-4.1','modelValidated':True,'modelCredentialRef':'MODEL_REF','modelKeyMask':'••••abcd','status':'active','personality':'Moderate','mandate':copy.deepcopy(PERSONALITIES['Moderate'])}
  s['accounts']=[a];s['agentConfigs']=[g];s['chats'][g['id']]=[];return s
 def proposal(self,s,n,key):return self.w.proposal(s,{'agentId':s['agentConfigs'][0]['id'],'marketId':'BTC','direction':'Yes','amount':n,'reasoning':'Test','idempotencyKey':key})
 def reload(self):
  with self.store.session():return self.store.load()
 def count(self,table):
  with self.store.connect() as c:return c.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']

 def test_fresh_database_matches_seed(self):
  s=self.reload();seed=main_seed()
  self.assertEqual({k:v for k,v in s.items() if k!='clock'},{k:v for k,v in seed.items() if k!='clock'})
 def test_round_trip_is_lossless(self):
  s=self.workspace()
  with self.store.session():
   self.w.refresh(s,s['accounts'][0]);self.proposal(s,10,'filled');self.proposal(s,40,'pending')
   self.w.action(s,{'action':'chat','agent':s['agentConfigs'][0]['id'],'text':'Buy BTC'});self.store.save(s)
  self.assertEqual(self.reload(),s)
 def test_repeated_saves_do_not_duplicate_rows(self):
  s=self.workspace()
  with self.store.session():self.proposal(s,40,'pending');self.store.save(s)
  with self.store.session():
   s=self.store.load();o=s['orders'][0];self.w.action(s,{'action':'decision','id':o['id'],'approve':True});self.store.save(s);self.store.save(s)
  loaded=self.reload();self.assertEqual(loaded,s);self.assertEqual(loaded['orders'][0]['status'],'filled')
  self.assertEqual(self.count('order_evaluations'),2);self.assertEqual(self.count('events'),len(s['events']));self.assertEqual(self.count('orders'),1)
 def test_idempotency_survives_restart(self):
  s=self.workspace()
  with self.store.session():self.proposal(s,10,'same');self.store.save(s)
  with self.store.session():
   s=self.store.load();self.proposal(s,10,'same');self.store.save(s)
  self.assertEqual(self.v.calls,1);self.assertEqual(self.count('orders'),1)
 def test_crash_safe_reservation_is_persisted_before_submission(self):
  s=self.workspace();self.w.persist=self.store.save;seen=[]
  def place(o):
   # A separate connection sees only committed data: the budget reservation must already be durable.
   with self.store.connect() as c:seen.append(c.execute('SELECT status,committed FROM orders').fetchall())
   return {'order_id':'venue-1'}
  self.v.place_order=place
  with self.store.session():self.store.save(s);self.proposal(s,10,'one')
  self.assertEqual(seen,[[{'status':'submission unknown','committed':True}]])
 def test_server_uses_store_for_main_only(self):
  import server
  s=self.workspace()
  with patch.object(server,'STORE',self.store):
   with server.locked('main'):server.save('main',s)
   with server.locked('main'):self.assertEqual(server.read('main'),s)
   self.assertIn('demoAgents',server.read('demo'))
 def governor_batch(self,s,batch_id='batch-1',ids=None):
  # Real account/agent ids so the governor tables' foreign keys hold.
  from datetime import datetime,timedelta,timezone
  from decimal import Decimal as D
  from governor import records
  from governor.allocator import decide
  from governor.models import Portfolio,Proposal
  from governor.snapshot import build_snapshot
  now=datetime.now(timezone.utc).replace(microsecond=0);a=s['accounts'][0]['id'];g=s['agentConfigs'][0]['id']
  books=[{'id':'MKT','name':'MKT','eventTicker':'EVT','agent':'sports','venue':'kalshi','open':True,'executionReady':True,'feeMultiplier':1.0,'closeAt':None,'yesBids':[['0.36','500']],'noBids':[['0.63','500']],'asOf':now.isoformat()}]
  ids=ids or [f'{batch_id}-{i}' for i in range(2)]
  proposals=[Proposal(pid,g,'MKT',('yes','no')[i%2],D(('0.45','0.30')[i%2]),D(25),now,now+timedelta(seconds=60)) for i,pid in enumerate(ids)]
  for p in proposals:records.record_forecast(s,p)
  portfolio=Portfolio(D(1000),D(1000));decisions=decide(proposals,build_snapshot(books,snapshot_id='snap',taken_at=now),portfolio,{},now)
  batch=records.batch_record(batch_id=batch_id,account_id=a,started_at=now,evaluated_at=now,snapshot_id='snap',books=books,proposals=proposals,portfolio=portfolio,reliability={})
  records.record_batch(s,batch,decisions,now);return decisions
 def test_governor_records_round_trip_and_replay(self):
  from governor import records
  s=self.workspace()
  with self.store.session():decisions=self.governor_batch(s);self.store.save(s)
  loaded=self.reload();self.assertEqual(loaded,s)
  self.assertEqual([d.to_dict() for d in records.replay(loaded['governorBatches'][0])],[d.to_dict() for d in decisions])
 def test_governor_rows_written_once_and_outcomes_update(self):
  from governor import records
  s=self.workspace()
  with self.store.session():self.governor_batch(s);self.store.save(s);self.store.save(s)
  with self.store.session():
   s=self.store.load();self.assertEqual(records.resolve_forecasts(s,{'MKT':1},records._time(s['governorBatches'][0]['startedAt'])),1);self.store.save(s)
  loaded=self.reload();self.assertEqual(loaded,s);self.assertEqual(loaded['governorForecasts'][0]['outcome'],'1')
  self.assertEqual((self.count('governor_batches'),self.count('governor_decisions'),self.count('governor_forecasts')),(1,2,1))
 def test_governor_ids_reload_in_code_point_order(self):
  # Locale collation would order "ab" before "a-c" and "a" before "B"; reloads must match state exactly.
  s=self.workspace()
  with self.store.session():self.governor_batch(s,ids=['ab','a-c','B']);self.store.save(s)
  loaded=self.reload();self.assertEqual(loaded,s);self.assertEqual([d['proposalId'] for d in loaded['governorDecisions']],['ab','a-c','B'])
 def test_breaker_events_are_permanent_beyond_the_state_window(self):
  from datetime import datetime,timedelta,timezone
  from governor import records
  s=self.workspace();a=s['accounts'][0]['id'];start=datetime.now(timezone.utc).replace(microsecond=0)
  with self.store.session():
   for i in range(records.BREAKER_EVENT_WINDOW+5):records.record_breaker_event(s,a,{'action':'breakerReset','at':(start+timedelta(seconds=i)).isoformat(),'n':i},f'ev-{i:03d}')
   self.store.save(s);self.store.save(s)
  # State holds all 205 until saved; a reload returns the newest window while Postgres keeps every row.
  loaded=self.reload();self.assertEqual(loaded,{**s,'governorBreakerEvents':s['governorBreakerEvents'][:records.BREAKER_EVENT_WINDOW]})
  self.assertEqual((len(loaded['governorBreakerEvents']),self.count('governor_breaker_events')),(records.BREAKER_EVENT_WINDOW,records.BREAKER_EVENT_WINDOW+5))
 def test_earlier_forecast_replaces_the_stored_row(self):
  from datetime import datetime,timedelta,timezone
  from decimal import Decimal as D
  from governor import records
  from governor.models import Proposal
  s=self.workspace();g=s['agentConfigs'][0]['id'];t=datetime.now(timezone.utc).replace(microsecond=0)
  late=Proposal('late',g,'MKT','yes',D('0.4'),D(5),t+timedelta(seconds=5),t+timedelta(seconds=60))
  early=Proposal('early',g,'MKT','yes',D('0.9'),D(5),t,t+timedelta(seconds=60))
  with self.store.session():records.record_forecast(s,late);self.store.save(s)
  with self.store.session():s=self.store.load();self.assertTrue(records.record_forecast(s,early));self.store.save(s)
  loaded=self.reload();self.assertEqual(loaded,s);self.assertEqual([f['proposalId'] for f in loaded['governorForecasts']],['early'])
  self.assertEqual(self.count('governor_forecasts'),1)
 def test_reusing_an_id_outside_the_state_window_fails_loudly(self):
  from datetime import datetime,timedelta,timezone
  from governor import records
  s=self.workspace();a=s['accounts'][0]['id'];t=datetime.now(timezone.utc).replace(microsecond=0)
  batch=lambda i:{'id':f'batch-{i:03d}','accountId':a,'startedAt':(t+timedelta(seconds=i)).isoformat(),'policyVersion':'v','inputs':{}}
  event=lambda i:{'action':'breakerReset','at':(t+timedelta(seconds=i)).isoformat()}
  with self.store.session():
   for i in range(records.BATCH_WINDOW+1):records.record_batch(s,batch(i),[],t)
   for i in range(records.BREAKER_EVENT_WINDOW+1):records.record_breaker_event(s,a,event(i),f'ev-{i:03d}')
   self.store.save(s)
  for reuse in (lambda s:records.record_batch(s,batch(0),[],t),lambda s:records.record_breaker_event(s,a,event(0),'ev-000')):
   with self.store.session():
    s=self.store.load();reuse(s)  # the original left the loaded window, so state accepts the id
    with self.assertRaises(Exception):self.store.save(s)
  self.assertEqual((self.count('governor_batches'),self.count('governor_breaker_events')),(records.BATCH_WINDOW+1,records.BREAKER_EVENT_WINDOW+1))
 def test_saving_governor_records_without_a_load_is_refused_clearly(self):
  s=self.workspace()
  with self.store.session():self.governor_batch(s);self.store.save(s)
  with self.store.session():
   with self.assertRaisesRegex(ValueError,'Load the workspace'):self.store.save(s)
  self.assertEqual(self.count('governor_batches'),1)
 def test_forecasts_missing_from_state_are_not_deleted(self):
  # Only forecasts record_forecast replaced are deleted, never rows that are merely absent (e.g. a future load LIMIT).
  from datetime import datetime,timedelta,timezone
  from decimal import Decimal as D
  from governor import records
  from governor.models import Proposal
  s=self.workspace();g=s['agentConfigs'][0]['id'];t=datetime.now(timezone.utc).replace(microsecond=0)
  with self.store.session():
   for m in ('M1','M2'):records.record_forecast(s,Proposal(f'f-{m}',g,m,'yes',D('0.5'),D(5),t,t+timedelta(seconds=60)))
   self.store.save(s)
  with self.store.session():s=self.store.load();s['governorForecasts'].pop();self.store.save(s)
  self.assertEqual(self.count('governor_forecasts'),2)

