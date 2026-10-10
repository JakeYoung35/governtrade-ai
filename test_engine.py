import unittest, copy, json, tempfile
from concurrent.futures import ThreadPoolExecutor
from engine import *
class GovernanceTests(unittest.TestCase):
 def trade(self,s,n=10,key=None,agent='crypto',mandate=None):return propose(s,agent,next(m for m in s['markets'] if m['agent']==agent),n,'Yes','Test',key or uid(),mandate)
 def test_fill_account(self):
  s=seed();o=self.trade(s);self.assertEqual(o['status'],'filled');self.assertEqual(s['cash'],990);self.assertEqual(s['positions'][0]['value'],9.9);self.assertEqual(s['reserved'],0)
 def test_idempotency(self):
  s=seed();a=self.trade(s,key='same');b=self.trade(s,key='same');self.assertEqual(a['id'],b['id']);self.assertEqual(len(s['orders']),1);self.assertEqual(s['cash'],990)
 def test_approval_and_denial(self):
  for approve in [True,False]:
   s=seed();o=self.trade(s,40);self.assertEqual(o['status'],'awaiting approval');self.assertEqual(s['cash'],1000);action(s,{'action':'decision','id':o['id'],'approve':approve});self.assertEqual(o['status'],'filled' if approve else 'denied');self.assertEqual(s['cash'],960 if approve else 1000)
 def test_hard_block(self):
  s=seed();o=self.trade(s,950);self.assertEqual(o['decision'],'BLOCK');action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(s['cash'],1000)
 def test_fresh_check(self):
  s=seed();o=self.trade(s,40);s['permissions']['minimum']=990;action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'blocked')
 def test_shared_daily(self):
  s=seed();s['permissions']['dailyCap']=15;self.trade(s,10);o=self.trade(s,10,agent='sports');self.assertEqual(o['status'],'blocked')
 def test_split_market_cap(self):
  s=seed();s['permissions']['marketCap']=15;self.trade(s,10);self.assertEqual(self.trade(s,10)['status'],'blocked')
 def test_mandate_cap_and_fees(self):
  s=seed();action(s,{'action':'mandate','budget':100,'perMarket':5,'days':5,'frequency':24});m=s['mandates'][0];a=self.trade(s,5,mandate=m['id']);b=self.trade(s,1,mandate=m['id']);self.assertEqual(a['status'],'filled');self.assertEqual(b['status'],'blocked');self.assertEqual(m['spent'],5)
 def test_schedule_and_expiry(self):
  s=seed();action(s,{'action':'mandate','budget':100,'perMarket':5,'days':5,'frequency':24});advance(s,24);self.assertEqual(len(s['orders']),2);advance(s,96);self.assertEqual(s['mandates'][0]['status'],'expired');self.assertEqual(len(s['orders']),2)
 def test_paused(self):
  s=seed();action(s,{'action':'mandate','budget':100,'perMarket':5,'days':5,'frequency':24});s['mandates'][0]['status']='paused';advance(s,24);self.assertFalse(s['orders'])
 def test_schedule_new_permissions(self):
  s=seed();action(s,{'action':'mandate','budget':100,'perMarket':10,'days':5,'frequency':24});s['permissions']['autoLimit']=5;advance(s,24);self.assertTrue(all(o['status']=='awaiting approval' for o in s['orders']));self.assertEqual(s['cash'],1000)
 def test_stale_approval(self):
  s=seed();o=self.trade(s,40);advance(s,1);action(s,{'action':'decision','id':o['id'],'approve':True});self.assertEqual(o['status'],'blocked')
 def test_parser_clarifies(self):
  s=seed();action(s,{'action':'chat','text':'Invest $10 in the 15-minute crypto market.','key':uid()});self.assertFalse(s['orders']);self.assertIn('specify',s['chats']['crypto'][-1]['text'])
 def test_parser_executes(self):
  s=seed();action(s,{'action':'chat','text':'Invest $10 in BTC 15-minute Up/Down, Yes.','key':uid()});self.assertEqual(s['orders'][0]['status'],'filled')
 def test_settlement_reconciles(self):
  s=seed();self.trade(s);advance(s,1);self.assertEqual(round(s['cash']-s['deposits'],2),s['realized']);self.assertEqual(s['positions'][0]['status'],'settled')
 def test_disabled(self):
  s=seed();s['disabled']=['crypto'];self.assertEqual(self.trade(s)['status'],'blocked')
 def test_persistence(self):
  from server import save,read
  s=seed();self.trade(s);save('test',s);self.assertEqual(read('test'),s)
if __name__=='__main__':unittest.main()
