import copy,json,tempfile,unittest,os
from unittest.mock import patch
from pathlib import Path
from engine import main_seed,ceiling
from workspace import Workspace
from vault import Vault
from test_engine import FakeVenue,FakeVault

class ReviewTests(unittest.TestCase):
 def setUp(self):
  self.s=main_seed();self.v=FakeVenue();self.w=Workspace(FakeVault());self.w.venue=lambda a:self.v
  self.a={'id':'original','venue':'kalshi','environment':'sandbox','status':'connected','ceiling':{}}
  self.s['accounts']=[self.a]
 def test_replacement_keys_records_subaccounts_cannot_clear_observed_loss(self):
  self.w.refresh(self.s,self.a);self.v.cash=920;self.w.refresh(self.s,self.a)
  persisted=json.loads(json.dumps(self.s));persisted['accounts']=[]
  replacement={**self.a,'id':'new','subaccount':2};replacement.pop('baseline');replacement.pop('balance');persisted['accounts']=[replacement]
  self.v.cash=920;self.assertEqual(self.w.refresh(persisted,replacement)['dailyLoss'],80)
  self.v.cash=1000;self.assertEqual(self.w.refresh(persisted,replacement)['dailyLoss'],80)
 def test_new_utc_day_resets_loss_and_live_is_separate(self):
  self.w.refresh(self.s,self.a);self.v.cash=920;self.w.refresh(self.s,self.a)
  live={**self.a,'id':'live','environment':'live'};live.pop('baseline');self.s['accounts'].append(live)
  self.assertEqual(self.w.refresh(self.s,live)['dailyLoss'],0)
  with patch('workspace.now',return_value='2099-01-01T00:00:00+00:00'):
   self.assertEqual(self.w.refresh(self.s,self.a)['dailyLoss'],0)
 def test_new_connections_inherit_and_explicit_override_tightens(self):
  class Store(FakeVault):
   def put(self,*args):return 'ref'
  self.w.vault=Store()
  with patch('workspace.adapter'):
   self.w.action(self.s,{'action':'account-save','name':'New','connectionType':'kalshi-demo','credentials':{'key_id':'id','private_key':'pem'}})
  a=self.s['accounts'][-1];self.assertEqual(a['ceiling'],{})
  self.s['permissions']['dailyCap']=200;self.assertEqual(ceiling(self.s,a)['dailyCap'],200)
  a['ceiling']['dailyCap']=50;self.assertEqual(ceiling(self.s,a)['dailyCap'],50)
 def test_original_keychain_minus50_and_locked_and_write_errors_fail_closed(self):
  import keyring
  backend=type('MacBackend',(),{'__module__':'keyring.backends.macOS'})()
  for method,error in [('get_password',keyring.errors.KeyringError("Can't get password from keychain: (-50, 'Unknown Error')")),('get_password',keyring.errors.KeyringLocked('private')),('set_password',keyring.errors.PasswordSetError('private'))]:
   with self.subTest(method=method),tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True),patch('keyring.get_keyring',return_value=backend),patch('keyring.get_password',return_value=None),patch('keyring.'+method,side_effect=error):
    with self.assertRaisesRegex(ValueError,'macOS Keychain refused access'):Vault(d).put({'private_key':'secret'})
    self.assertFalse((Path(d)/'credentials.enc').exists());self.assertFalse((Path(d)/'credentials.tmp').exists())
