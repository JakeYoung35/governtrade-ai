import copy,json,os,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from engine import *
from adapters import KalshiAdapter,CoinbaseAdapter,VenueError,now
from workspace import Workspace
from vault import Vault
class GovernanceTests(unittest.TestCase):
 def trade(self,s,n=10,key=None,agent='crypto'):return propose(s,agent,next(m for m in s['markets'] if m['agent']==agent),n,'Yes','Test',key or uid())
 def test_fill_account(self):
  s=seed();o=self.trade(s);self.assertEqual(o['status'],'filled');self.assertEqual(s['cash'],990);self.assertEqual(s['positions'][0]['value'],9.9)
 def test_idempotency(self):
  s=seed();self.trade(s,key='same');self.trade(s,key='same');self.assertEqual(len(s['orders']),1);self.assertEqual(s['cash'],990)
 def test_approval_and_denial(self):
  for approve in [True,False]:
   s=seed();o=self.trade(s,40,agent='sports');self.assertEqual(o['status'],'awaiting approval');action(s,{'action':'decision','id':o['id'],'approve':approve});self.assertEqual(o['status'],'filled' if approve else 'denied')
 def test_hard_block(self):
  s=seed();o=self.trade(s,950);action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['decision'],'BLOCK');self.assertEqual(s['cash'],1000)
 def test_fresh_check(self):
  s=seed();o=self.trade(s,40);s['permissions']['minimum']=990;action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'blocked')
 def test_shared_daily(self):
  s=seed();s['permissions']['dailyCap']=15;self.trade(s,10);self.assertEqual(self.trade(s,10,agent='sports')['status'],'blocked')
 def test_split_market_cap(self):
  s=seed();s['permissions']['marketCap']=15;self.trade(s,10);self.assertEqual(self.trade(s,10)['status'],'blocked')
 def test_loss_block_cannot_be_overridden(self):
  s=seed();s['realized']=-80;o=self.trade(s);self.assertEqual(checks(s,o,True)['outcome'],'BLOCK');self.assertEqual(s['cash'],1000)
 def test_rule_change_pauses_next_trade(self):
  s=seed();action(s,{'action':'toggle-agent','agent':'crypto'});self.assertEqual(self.trade(s)['status'],'blocked')
 def test_no_recurring_engine(self):
  s=seed();advance(s,24);self.assertFalse(s['orders']);self.assertNotIn('mandates',s);self.assertNotIn('recurring',DEFAULT)
 def test_stale_approval(self):
  s=seed();o=self.trade(s,40);advance(s,1);action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'blocked')
 def test_parser_clarifies(self):
  s=seed();action(s,{'action':'chat','text':'Invest $10 in the 15-minute crypto market.','key':uid()});self.assertFalse(s['orders'])
 def test_parser_executes(self):
  s=seed();action(s,{'action':'chat','text':'Invest $10 in BTC 15-minute Up/Down, Yes.','key':uid()});self.assertEqual(s['orders'][0]['status'],'filled')
 def test_settlement_reconciles(self):
  s=seed();self.trade(s);advance(s,1);self.assertEqual(round(s['cash']-s['deposits'],2),s['realized'])
 def test_persistence_and_isolation(self):
  import server
  with tempfile.TemporaryDirectory() as d,patch.object(server,'DATA',Path(d)):
   s=seed();self.trade(s);server.save('demo',s);self.assertEqual(server.read('demo'),s);self.assertFalse(server.read('main')['accounts']);self.assertNotIn('cash',server.read('main'))

class FakeVault:
 def get(self,k):return {}
 def delete(self,k):pass
class FakeVenue:
 def __init__(self):self.cash=1000;self.calls=0;self.fail=False;self.price=.5;self.exposure=0;self.category='crypto'
 def get_balance_positions(self):
  if self.fail:raise VenueError('failure')
  return {'cash':self.cash,'positionValue':0,'exposure':self.exposure,'realizedPnl':0,'unrealizedPnl':0,'positions':[],'asOf':now(),'mock':False,'source':'test'}
 def get_quote(self,k,d):return {'id':k,'name':'BTC','agent':self.category,'venue':'kalshi','instrument':'prediction','price':self.price,'quoteAt':now(),'closeAt':'2099-01-01T00:00:00+00:00','open':True,'executionReady':True,'slippage':0}
 def place_order(self,o):self.calls+=1;return {'order_id':'venue-1','fill_count':'1','average_fill_price':'.5','average_fee_paid':'.01'}
class MainTests(unittest.TestCase):
 def setUp(self):
  self.s=main_seed();self.a={'id':'account','name':'Kalshi','venue':'kalshi','environment':'sandbox','status':'connected','credentialRef':'SECRET_REF','permissions':{}};self.g={'id':'agent','name':'Agent','accountId':'account','category':'crypto','status':'active','permissions':{},'modelCredentialRef':'MODEL_REF'};self.s['accounts']=[self.a];self.s['agentConfigs']=[self.g];self.v=FakeVenue();self.w=Workspace(FakeVault());self.w.venue=lambda a:self.v
 def proposal(self,n=10,key='one'):return self.w.proposal(self.s,{'agentId':'agent','marketId':'BTC','direction':'Yes','amount':n,'reasoning':'Test reasoning','idempotencyKey':key})
 def test_main_fresh_balance_and_once(self):
  o=self.proposal();self.assertEqual(o['status'],'filled');self.proposal();self.assertEqual(self.v.calls,1);self.assertEqual(o['amount'],10)
 def test_stale_balance_blocks(self):
  self.w.refresh(self.s,self.a);self.v.fail=True;o=self.proposal();self.assertEqual(o['decision'],'BLOCK');self.assertEqual(self.v.calls,0);self.assertTrue(self.a['stale']);self.assertEqual(self.a['balance']['cash'],1000)
 def test_fresh_low_cash_blocks(self):
  self.w.refresh(self.s,self.a);self.v.cash=105;self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_approval_rechecks_pause(self):
  o=self.proposal(40);self.g['status']='paused';self.w.action(self.s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['decision'],'BLOCK');self.assertEqual(self.v.calls,0)
 def test_external_exposure_blocks(self):
  self.v.exposure=495;self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_observed_daily_loss_blocks(self):
  self.w.refresh(self.s,self.a);self.v.cash=920;self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_stricter_override(self):
  self.g['permissions']={'dailyCap':5,'minimum':0};self.assertEqual(rules(self.s,self.g)['minimum'],100);self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_shared_budget_across_agents(self):
  self.s['permissions']['dailyCap']=15;self.proposal();g={**self.g,'id':'second'};self.s['agentConfigs'].append(g);o=self.w.proposal(self.s,{'agentId':'second','marketId':'BTC','direction':'Yes','amount':10,'reasoning':'Test','idempotencyKey':'two'});self.assertEqual(o['decision'],'BLOCK')
 def test_submission_unknown_keeps_reserve(self):
  self.v.place_order=lambda o:(_ for _ in ()).throw(VenueError('timeout'));o=self.proposal();self.assertEqual(o['status'],'submission unknown');self.assertEqual(usage(self.s,'account')['held'],10);self.proposal();self.assertEqual(len(self.s['orders']),1)
 def test_category_venue_verified(self):
  self.v.category='sports';self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_public_config_redacts_refs(self):
  data=json.dumps(self.w.public(self.s));self.assertNotIn('SECRET_REF',data);self.assertNotIn('MODEL_REF',data)
 def test_nan_invalid(self):
  with self.assertRaises(ValueError):self.proposal(float('nan'))
  with self.assertRaises(ValueError):validate_permissions({'dailyCap':float('inf')})
 def test_price_change_needs_review(self):
  o=self.proposal(40);self.v.price=.6;self.w.action(self.s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'awaiting approval');self.assertEqual(self.v.calls,0)

class AdapterTests(unittest.TestCase):
 def test_kalshi_no_order_mapping_and_budget(self):
  v=KalshiAdapter({});calls=[];v.request=lambda *a,**k:calls.append((a,k)) or {'fill_count':'0'}
  v.place_order({'id':'uuid','market':{'id':'BTC','price':.4,'feeMultiplier':1},'amount':10,'direction':'No'})
  payload=calls[0][0][2];self.assertEqual(payload['side'],'ask');self.assertEqual(payload['price'],'0.6');self.assertEqual(payload['time_in_force'],'immediate_or_cancel');self.assertLessEqual(int(payload['count'])*.43,10)
 def test_coinbase_live_limit_and_fee_preview(self):
  v=CoinbaseAdapter({},'live');calls=[]
  def request(method,path,body=None,**kwargs):
   calls.append((method,path,body))
   if 'transaction_summary' in path:return {'fee_tier':{'taker_fee_rate':'.006'}}
   if path=='/orders/preview':return {'preview_id':'preview','commission_total':'.06','errs':[]}
   return {'success':True,'success_response':{'order_id':'order'}}
  v.request=request;v.place_order({'id':'uuid','market':{'id':'BTC-USD','price':100,'baseIncrement':'.00001'},'amount':10})
  configuration=calls[-1][2]['order_configuration']['limit_limit_fok'];self.assertLessEqual(float(configuration['base_size'])*100+.06,10);self.assertEqual(configuration['limit_price'],'100');self.assertEqual(calls[-1][2]['preview_id'],'preview')
 def test_preview_over_budget_never_submits(self):
  v=CoinbaseAdapter({},'live');calls=[]
  def request(method,path,body=None,**kwargs):
   calls.append(path)
   return {'fee_tier':{'taker_fee_rate':'.006'}} if 'transaction_summary' in path else {'preview_id':'preview','commission_total':'20','errs':[]}
  v.request=request
  with self.assertRaises(VenueError):v.place_order({'id':'uuid','market':{'id':'BTC-USD','price':100,'baseIncrement':'.00001'},'amount':10})
  self.assertNotIn('/orders',calls)
 def test_coinbase_sandbox_is_mock(self):
  v=CoinbaseAdapter({});v.request=lambda *a,**k:{'accounts':[{'currency':'USD','available_balance':{'value':'50'},'hold':{'value':'0'}}]};b=v.get_balance_positions();self.assertTrue(b['mock']);self.assertIsNone(b['realizedPnl']);self.assertEqual(b['cash'],50)
 def test_crypto_keys_encrypted_and_redacted(self):
  try:from cryptography.fernet import Fernet
  except ImportError:self.skipTest('Install requirements.txt to test encrypted vault')
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'GOVERNTRADE_MASTER_KEY':Fernet.generate_key().decode()}):
   v=Vault(d);ref=v.put({'api_key':'very-secret-model-key'});raw=(Path(d)/'credentials.enc').read_bytes();self.assertNotIn(b'very-secret',raw);self.assertEqual(v.get(ref)['api_key'],'very-secret-model-key');v.delete(ref);self.assertEqual(v.read(),{})
if __name__=='__main__':unittest.main()
