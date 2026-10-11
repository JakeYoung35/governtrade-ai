import copy,json,os,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from engine import *
from adapters import KalshiAdapter,VenueError,now
from workspace import Workspace
from vault import Vault
class GovernanceTests(unittest.TestCase):
 def trade(self,s,n=10,key=None,agent='demo-conservative'):return propose(s,agent,next(m for m in s['markets'] if m['agent']==next(a['demoMarket'] for a in s['demoAgents'] if a['id']==agent)),n,'Yes','Test',key or uid())
 def test_fill_account(self):
  s=seed();o=self.trade(s);self.assertEqual(o['status'],'filled');self.assertEqual(s['cash'],990);self.assertEqual(s['positions'][0]['value'],9.9)
 def test_idempotency(self):
  s=seed();self.trade(s,key='same');self.trade(s,key='same');self.assertEqual(len(s['orders']),1);self.assertEqual(s['cash'],990)
 def test_approval_and_denial(self):
  for approve in [True,False]:
   s=seed();o=self.trade(s,40,agent='demo-moderate');self.assertEqual(o['status'],'awaiting approval');action(s,{'action':'decision','id':o['id'],'approve':approve});self.assertEqual(o['status'],'filled' if approve else 'denied')
 def test_hard_block(self):
  s=seed();o=self.trade(s,950);action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['decision'],'BLOCK');self.assertEqual(s['cash'],1000)
 def test_fresh_check(self):
  s=seed();o=self.trade(s,40,agent='demo-moderate');s['demoAgents'][1]['mandate']['minimum']=990;action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'blocked')
 def test_shared_daily(self):
  s=seed();s['permissions']['dailyCap']=95;self.trade(s,10);self.assertEqual(self.trade(s,10,agent='demo-moderate')['status'],'blocked')
 def test_split_market_cap(self):
  s=seed();s['safety']['marketCap']=15;self.trade(s,10);self.assertEqual(self.trade(s,10)['status'],'blocked')
 def test_loss_block_cannot_be_overridden(self):
  s=seed();s['realized']=-80;o=self.trade(s);self.assertEqual(checks(s,o,True)['outcome'],'BLOCK');self.assertEqual(s['cash'],1000)
 def test_rule_change_pauses_next_trade(self):
  s=seed();action(s,{'action':'toggle-agent','agent':'demo-conservative'});self.assertEqual(self.trade(s)['status'],'blocked')
 def test_no_recurring_engine(self):
  s=seed();advance(s,24);self.assertFalse(s['orders']);self.assertNotIn('mandates',s);self.assertNotIn('recurring',DEFAULT)
 def test_stale_approval(self):
  s=seed();o=self.trade(s,40,agent='demo-moderate');advance(s,1);action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'blocked')
 def test_settlement_reconciles(self):
  s=seed();self.trade(s);advance(s,168);self.assertEqual(round(s['cash']-s['deposits'],2),s['realized'])
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
  self.s=main_seed();self.a={'id':'account','name':'Kalshi','venue':'kalshi','environment':'sandbox','status':'connected','credentialRef':'SECRET_REF','ceiling':{}};self.g={'id':'agent','name':'Agent','accountId':'account','status':'active','personality':'Moderate','mandate':copy.deepcopy(PERSONALITIES['Moderate']),'modelValidated':True,'modelCredentialRef':'MODEL_REF'};self.s['accounts']=[self.a];self.s['agentConfigs']=[self.g];self.v=FakeVenue();self.w=Workspace(FakeVault());self.w.venue=lambda a:self.v
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
 def test_agent_specific_cap(self):
  self.g['mandate']['dailyCap']=5;self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_shared_budget_across_agents(self):
  self.s['permissions']['dailyCap']=15;self.proposal();g={**self.g,'id':'second'};self.s['agentConfigs'].append(g);o=self.w.proposal(self.s,{'agentId':'second','marketId':'BTC','direction':'Yes','amount':10,'reasoning':'Test','idempotencyKey':'two'});self.assertEqual(o['decision'],'BLOCK')
 def test_submission_unknown_keeps_reserve(self):
  self.v.place_order=lambda o:(_ for _ in ()).throw(VenueError('timeout'));o=self.proposal();self.assertEqual(o['status'],'submission unknown');self.assertEqual(usage(self.s,'account')['held'],10);self.proposal();self.assertEqual(len(self.s['orders']),1)
 def test_no_category_filter(self):
  self.v.category='sports';self.assertEqual(self.proposal()['decision'],'ALLOW')
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
 def test_crypto_keys_encrypted_and_redacted(self):
  try:from cryptography.fernet import Fernet
  except ImportError:self.skipTest('Install requirements.txt to test encrypted vault')
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'GOVERNTRADE_MASTER_KEY':Fernet.generate_key().decode()}):
   v=Vault(d);ref=v.put({'api_key':'very-secret-model-key'});raw=(Path(d)/'credentials.enc').read_bytes();self.assertNotIn(b'very-secret',raw);self.assertEqual(v.get(ref)['api_key'],'very-secret-model-key');v.delete(ref);self.assertEqual(v.read(),{})

class FollowupTests(MainTests):
 def test_draft_cannot_trade_or_resume(self):
  self.g.update(status='draft',mandate=None)
  self.assertEqual(self.proposal()['decision'],'BLOCK')
  with self.assertRaises(ValueError):self.w.action(self.s,{'action':'agent-status','id':'agent','status':'active'})
 def test_mandate_ceiling_and_worker_boundary(self):
  with self.assertRaises(ValueError):validate_mandate({**PERSONALITIES['Moderate'],'dailyCap':101},100)
  with self.assertRaises(ValueError):self.w.proposal(self.s,{'agentId':'agent','marketId':'BTC','direction':'Yes','amount':10,'reasoning':'x','idempotencyKey':'x','mandate':{'dailyCap':999}})
 def test_kill_switch_pauses_every_agent(self):
  self.s['agentConfigs'].append({**self.g,'id':'other'})
  self.w.action(self.s,{'action':'permissions','values':{'automation':False}})
  self.assertTrue(all(a['status']=='paused' for a in self.s['agentConfigs']))
  self.assertEqual(self.proposal()['decision'],'BLOCK')
 def test_new_market_denied_and_layer_results(self):
  self.g['mandate']['newMarketApproval']=True;o=self.proposal()
  self.assertEqual(o['decision'],'REQUIRE_APPROVAL')
  self.assertEqual(o['evaluations'][-1]['layers']['agent_mandate'],'REQUIRE_APPROVAL')
  self.w.action(self.s,{'action':'decision','id':o['id'],'approve':False});self.assertEqual(self.v.calls,0)
 def test_save_agent_validates_then_requires_mandate(self):
  class Store(FakeVault):
   def put(self,value,ref=None):return 'encrypted-ref'
  self.w.vault=Store()
  with patch('workspace.validate_model_key') as validate:
   result=self.w.action(self.s,{'action':'agent-save','name':'New','provider':'openai','model':'gpt-4.1','modelKey':'secret','accountId':'account'})
   validate.assert_called_once_with('openai','gpt-4.1','secret')
  a=self.s['agentConfigs'][-1];self.assertEqual(a['status'],'draft');self.assertIsNone(a['mandate'])
  self.w.action(self.s,{'action':'mandate-save','id':result['agentId'],'personality':'Moderate','mandate':PERSONALITIES['Moderate']});self.assertEqual(a['status'],'active')
 def test_validation_failure_does_not_save(self):
  with patch('workspace.validate_model_key',side_effect=ValueError('Invalid')):
   with self.assertRaises(ValueError):self.w.action(self.s,{'action':'agent-save','name':'New','provider':'openai','model':'gpt-4.1','modelKey':'secret','accountId':'account'})
  self.assertEqual(len(self.s['agentConfigs']),1)
 def test_live_confirmation_required(self):
  with self.assertRaises(ValueError):self.w.action(self.s,{'action':'account-save','connectionType':'kalshi-live','name':'Live','credentials':{}})
 def test_disconnect_reports_no_credentials(self):
  self.s['accounts'].append({**self.a,'id':'other','credentialRef':'OTHER_REF','credentialMasks':{'key_id':'••••83df','private_key':'••••----'}})
  self.w.action(self.s,{'action':'account-disconnect','id':'other'})
  public={a['id']:a for a in self.w.public(self.s)['accounts']}
  self.assertTrue(public['account']['hasCredentials']);self.assertFalse(public['other']['hasCredentials']);self.assertNotIn('credentialRef',public['account'])
 def test_disconnected_account_requires_new_credentials(self):
  self.w.action(self.s,{'action':'account-disconnect','id':'account'})
  with self.assertRaisesRegex(ValueError,'Kalshi credentials are required'):self.w.action(self.s,{'action':'account-save','id':'account','connectionType':'kalshi-demo','name':'Kalshi'})
  with self.assertRaisesRegex(ValueError,'no saved credentials'):self.w.action(self.s,{'action':'account-test','id':'account'})
  self.assertEqual(self.g['status'],'paused')
  with self.assertRaisesRegex(ValueError,'Reconnect'):self.w.action(self.s,{'action':'agent-status','id':'agent','status':'active'})
  self.assertEqual(self.g['status'],'paused')
 def test_failed_connection_test_blocks_resume(self):
  self.g['status']='paused';self.a['status']='error'
  with self.assertRaisesRegex(ValueError,'Reconnect'):self.w.action(self.s,{'action':'agent-status','id':'agent','status':'active'})
  self.assertEqual(self.g['status'],'paused')
  self.w.action(self.s,{'action':'agent-status','id':'agent','status':'paused'});self.assertEqual(self.g['status'],'paused')
 def test_reconnected_account_allows_resume(self):
  self.g['status']='paused';self.a['status']='error';self.v.fail=True;self.w.action(self.s,{'action':'account-test','id':'account'});self.assertEqual(self.a['status'],'error')
  with self.assertRaises(ValueError):self.w.action(self.s,{'action':'agent-status','id':'agent','status':'active'})
  self.v.fail=False;self.w.action(self.s,{'action':'account-test','id':'account'});self.assertEqual(self.a['status'],'connected')
  self.w.action(self.s,{'action':'agent-status','id':'agent','status':'active'});self.assertEqual(self.g['status'],'active')

class BalanceTests(unittest.TestCase):
 def test_cents_pnl_and_scoped_reserve(self):
  v=KalshiAdapter({},subaccount=2);calls=[]
  def request(method,path,*args,**kw):
   calls.append(path)
   if '/balance?' in path:return {'balance':12345,'portfolio_value':600}
   if '/positions?' in path:return {'market_positions':[{'ticker':'OPEN','position_fp':'10','market_exposure_dollars':'4','realized_pnl_dollars':'2','fees_paid_dollars':'.2'}]}
   if '/settlements?' in path:return {'settlements':[{'ticker':'DONE','revenue':1000,'yes_total_cost_dollars':'6','no_total_cost_dollars':'0','fee_cost':'.1'}]}
   if '/fills?' in path:return {'fills':[{'ticker':'OPEN','fee_cost':'.2','created_time':'2000-01-01','count_fp':'10','yes_price_dollars':'.4','no_price_dollars':'.6'}]}
   if '/summary/' in path:return {'total_resting_order_value':99999}
   return {'orders':[{'remaining_count_fp':'5','side':'yes','yes_price_dollars':'.3'}]}
  v.request=request;b=v.get_balance_positions()
  self.assertEqual(b['cash'],123.45);self.assertEqual(b['positionValue'],6);self.assertEqual(b['reserved'],1.5);self.assertEqual(b['realizedPnl'],5.7);self.assertEqual(b['unrealizedPnl'],2)
  self.assertTrue(all('subaccount=2' in p for p in calls if '/summary/' not in p))
 def test_summary_cents_and_pagination(self):
  v=KalshiAdapter({});calls=[]
  def request(method,path,*args,**kw):
   calls.append(path)
   if '/balance?' in path:return {'balance':1000,'portfolio_value':0}
   if '/summary/' in path:return {'total_resting_order_value':250}
   key='market_positions' if 'positions?' in path else 'settlements' if 'settlements?' in path else 'fills'
   return {key:[],'cursor':'next' if 'positions?' in path and 'cursor=' not in path else ''}
  v.request=request;self.assertEqual(v.get_balance_positions()['reserved'],2.5);self.assertTrue(any('cursor=next' in p for p in calls))
 def test_provider_catalog_rejects_cross_provider_model(self):
  from providers import validate_model_key
  with self.assertRaises(ValueError):validate_model_key('google','gpt-4.1','secret')
 def test_all_four_demo_outcomes(self):
  for a,n,wanted in [('demo-conservative',5,'ALLOW'),('demo-moderate',40,'REQUIRE_APPROVAL'),('demo-aggressive',30,'BLOCK'),('demo-new-market',10,'REQUIRE_APPROVAL')]:
   s=seed();g=next(g for g in s['demoAgents'] if g['id']==a);m=next(m for m in s['markets'] if m['agent']==g['demoMarket']);o=propose(s,a,m,n,'Yes','reason',uid());self.assertEqual(o['decision'],wanted)
   if wanted=='BLOCK':self.assertEqual(checks(s,o,True)['outcome'],'BLOCK')

if __name__=='__main__':unittest.main()
