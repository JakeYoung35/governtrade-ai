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
  with self.store.connect() as c:c.execute('TRUNCATE events,balance_snapshots,chat_messages,idempotency_keys,order_evaluations,orders,agents,accounts');c.execute("UPDATE workspace_settings SET daily_cap=100,automation=true,extra='{}'")
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
