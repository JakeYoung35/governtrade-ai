"""User management and the narrow, governed agent integration boundary."""
import copy, datetime, math, re, time, uuid
from adapters import adapter,ADAPTERS,VenueError,now
from engine import main_seed,checks,event,rules,validate_permissions,validate_mandate,ceiling
import trading_rules
from vault import masks
from providers import validate_model_key,validate_free_agent,catalog,FREE_PROVIDER
import order_views

TRANSIENT_SYNC_GRACE_SECONDS=120  # how long a connected account rides out unanswered balance reads before showing an error
BALANCE_RETRY_SECONDS=0.5
STATE_REFRESH_SECONDS=30  # how old a balance attempt may be before a page's state read re-reads the venue
LOOK_FOCI=('general','crypto','sports','politics','economics','culture')

def validate_look(look):
    """An agent's creature: a seed the front end draws it from, and the market focus that picks its accessory.
    It is cosmetic; nothing in governance reads it."""
    if not isinstance(look,dict) or set(look)!={'seed','focus'}:raise ValueError('An agent look needs a seed and a market focus.')
    if not isinstance(look['seed'],str) or not re.fullmatch(r'[a-z0-9]{6,12}',look['seed']):raise ValueError('The agent look seed must be 6 to 12 lowercase letters or digits.')
    if look['focus'] not in LOOK_FOCI:raise ValueError('Choose a market focus: '+', '.join(LOOK_FOCI)+'.')
    return {'seed':look['seed'],'focus':look['focus']}

class Workspace:
    def __init__(self,vault,persist=lambda s:None):self.vault=vault;self.persist=persist
    def venue(self,a):return adapter(a['venue'],self.vault.get(a['credentialRef']),a['environment'],a.get('subaccount',0))
    def refresh(self,s,a,test=False):
        # test: the user's "Test connection" reports every failure as an error instead of riding out a transient one.
        b,failure=self.read_balance(a);return self.apply_balance(s,a,b,'refused' if test else failure)
    def refresh_due(self,s,maxAge=0):
        # Reads each Kalshi account whose last attempt is at least maxAge seconds old (0: every account).
        # Returns whether any account was read, so a caller only saves when the state changed.
        synced=False
        for a in s['accounts']:
            if a['venue']=='kalshi' and a['status'] not in ('disconnected','removed') and a.get('credentialRef') and self.sync_due(a,maxAge):self.refresh(s,a);synced=True
        return synced
    def sync_due(self,a,maxAge):
        # Page polls read the state every few seconds; the window stops each poll from reaching the venue again.
        last=a.get('syncAttemptedAt')
        if last is None or maxAge<=0:return True
        return (datetime.datetime.fromisoformat(now())-datetime.datetime.fromisoformat(last)).total_seconds()>=maxAge
    def read_balance(self,a):
        # Venue read only, no state changes, so the governor can fetch outside the workspace lock.
        # Returns (balance, None), or (None, failure) where failure is 'transient' (no answer, HTTP 429 or 5xx)
        # or 'refused' (credentials, permissions or an invalid balance). A fast transient failure is retried once;
        # a timeout is not, because callers hold the workspace lock and a retry would double the wait.
        for attempt in range(2):
            try:
                b=self.venue(a).get_balance_positions()
                if not all(math.isfinite(float(b[k])) and float(b[k])>=0 for k in ('cash','positionValue','exposure')):raise VenueError('Venue returned invalid balance values.')
                return b,None
            except VenueError as e:
                if not e.transient:return None,'refused'
                if e.timed_out:return None,'transient'
                if attempt==0:time.sleep(BALANCE_RETRY_SECONDS)
            except TimeoutError:return None,'transient'
            except OSError:
                if attempt==0:time.sleep(BALANCE_RETRY_SECONDS)
            except (ValueError,KeyError,TypeError,ImportError):return None,'refused'
        return None,'transient'
    def apply_balance(self,s,a,b,failure='refused'):
        a['syncAttemptedAt']=now()  # recorded for failures too, so an unreachable venue is not retried on every poll
        if b is None:
            # A connected account rides out transient failures for a short grace period, however many reads fail
            # in it: it stays connected, so agents can still propose, but the balance is marked stale, so every
            # order and governor batch waits for a fresh one.
            since=a.setdefault('syncFailingSince',now())
            failing=(datetime.datetime.fromisoformat(now())-datetime.datetime.fromisoformat(since)).total_seconds()
            if failure=='transient' and a.get('status')=='connected' and failing<TRANSIENT_SYNC_GRACE_SECONDS:
                a.update(stale=True,error='Kalshi did not answer the last balance check. Trades wait until a fresh balance arrives.')
            else:a.update(status='error',stale=True,error='Balance sync failed. Check credentials, environment, permissions and network.')
            return None
        equity=b['cash']+b.get('reserved',0)+b['positionValue'];day=now()[:10]
        if a.get('baseline',{}).get('day')!=day:a['baseline']={'day':day,'equity':equity}
        b['dailyLoss']=max(0,a['baseline']['equity']-equity);a.pop('syncFailingSince',None);a.update(balance=b,status='connected',stale=False,error=None);return b
    def evaluate(self,s,o,approved=False):
        a=next(x for x in s['accounts'] if x['id']==o['accountId']);v=self.venue(a);s['clock']=now();b=self.refresh(s,a)
        try:m=v.get_quote(o['market']['id'],o['direction'])
        except ValueError:m={**o['market'],'open':False,'executionReady':False,'executionNote':'Fresh quote unavailable.'}
        # Approval authorizes this exact market/direction/budget. A price increase needs fresh approval.
        if approved and m['price']>o['market']['price']:
            o['priceChanged']=True;approved=False
        s['clock']=now();o['market']=m;o['_balance']=b;e=checks(s,o,approved);
        if o.pop('priceChanged',False) and e['outcome']=='ALLOW':
            e['checks'].append({'name':'Price changed','layer':'execution','outcome':'REQUIRE_APPROVAL','reason':'Price increased; review the updated exact proposal.'});e['outcome']='REQUIRE_APPROVAL';e['layers']['execution']='REQUIRE_APPROVAL'
        o.pop('_balance',None);o['evaluations'].append(e);o['decision']=e['outcome'];o['status']={'ALLOW':'submitted','BLOCK':'blocked','REQUIRE_APPROVAL':'awaiting approval'}[e['outcome']]
        event(s,f"{m['name']} · ${o['amount']:.2f} · {e['outcome']}",e['outcome'],o['id'])
        if e['outcome']=='ALLOW':
            o['committed']=True;o['status']='submission unknown';self.persist(s) # crash-safe budget reservation before network request
            try:
                response=v.place_order(o);o['executedAt']=now() # venue response time; balance reads that start later include this order
                if not response.get('success',True):o.update(status='rejected',committed=False)
                else:
                    o['venueOrderId']=response.get('order_id') or response.get('success_response',{}).get('order_id');o['status']='submitted';o['automatic']=not approved
                    if 'fill_count' in response:
                        filled=float(response['fill_count']);o.update(status='filled' if filled else 'cancelled',filled=filled>0,quantity=filled)
                        if filled:
                            fill_price=float(response.get('average_fill_price',0));fill_price=1-fill_price if o['direction']=='No' else fill_price;cost=filled*(fill_price+float(response.get('average_fee_paid',0)));o['actualCost']=round(cost,2)
                        else:o['committed']=False
                event(s,'Venue submission result recorded.','SUBMITTED',o['id'])
            except ValueError:event(s,'Submission result unknown; budget remains reserved. Reconcile with venue before retrying.','UNKNOWN',o['id'])
            self.refresh(s,a)
        return o
    def proposal(self,s,b,governed=None):
        # governed: exact {'contracts','limitPrice'} passed server-side by the portfolio governor, never by workers.
        if set(b)-{'agentId','marketId','direction','amount','reasoning','idempotencyKey'}:raise ValueError('Workers may only submit trade terms, never mandates, ceilings or approvals.')
        key=b.get('idempotencyKey','')
        if not isinstance(key,str) or not 1<=len(key)<=200:raise ValueError('An idempotencyKey is required.')
        agent=next(x for x in s['agentConfigs'] if x['id']==b['agentId']);scoped=agent['id']+':'+key
        if scoped in s['keys']:return next(x for x in s['orders'] if x['id']==s['keys'][scoped])
        amount=b['amount']
        if type(amount) not in (int,float) or not math.isfinite(amount) or not 1<=amount<=1000000:raise ValueError('Use a finite all-in USD budget between $1 and $1,000,000.')
        if not isinstance(b.get('marketId'),str) or not isinstance(b.get('reasoning'),str) or len(b['reasoning'])>8000:raise ValueError('marketId and agent reasoning are required.')
        a=next(x for x in s['accounts'] if x['id']==agent['accountId']);v=self.venue(a);m=v.get_quote(b['marketId'],b['direction']);o={'id':str(uuid.uuid4()),'agent':agent['id'],'accountId':a['id'],'market':m,'amount':round(amount,2),'direction':b['direction'],'request':b['reasoning'],'time':now(),'evaluations':[],'filled':False,**({'governedTerms':dict(governed)} if governed else {})};s['orders'].insert(0,o);s['keys'][scoped]=o['id'];return self.evaluate(s,o)
    def action(self,s,b):
        action=b['action'];s['clock']=now()
        if action=='refresh':self.refresh_due(s)
        elif action=='account-save':
            kind=b.get('connectionType');environment={'kalshi-demo':'sandbox','kalshi-live':'live'}.get(kind)
            if not environment:raise ValueError('Choose Kalshi Demo or Kalshi Live.')
            if environment=='live' and b.get('confirmLive') is not True:raise ValueError('Confirm that Kalshi Live uses real money before connecting.')
            subaccount=b.get('subaccount',0)
            if type(subaccount)!=int or not 0<=subaccount<=63:raise ValueError('Subaccount must be an integer from 0 to 63.')
            a=next((a for a in s['accounts'] if a['id']==b.get('id')),None);credentials=b.get('credentials')
            if a and a.get('status')=='removed':raise ValueError('This Kalshi connection was removed.')
            if a and (a['environment']!=environment or a.get('subaccount',0)!=subaccount):raise ValueError('Create a new connection to change Demo/Live or subaccount; audit history keeps its original account.')
            if not a and not credentials:raise ValueError('Kalshi credentials are required.')
            if a and not credentials and not a.get('credentialRef'):raise ValueError('Kalshi credentials are required. Enter the API key ID and private key.')
            if credentials:
                if set(credentials)!={'key_id','private_key'} or any(not isinstance(v,str) or not v.strip() for v in credentials.values()):raise ValueError('Enter the Kalshi key ID and PEM private key.')
                for existing in s['accounts']:
                    if existing.get('credentialRef') and existing['id']!=b.get('id') and existing['venue']=='kalshi' and existing['environment']==environment and existing.get('subaccount',0)==subaccount and self.vault.get(existing['credentialRef']).get('key_id')==credentials['key_id']:raise ValueError('This Kalshi key and subaccount are already connected.')
                test=adapter('kalshi',credentials,environment,subaccount);test.headers('GET',test.base+'/portfolio/balance')
            if a and any(o.get('committed') and o['accountId']==a['id'] and o['status'] in ('submitted','open','submission unknown') for o in s['orders']):raise ValueError('Reconcile outstanding submissions before rotating keys.')
            reference=self.vault.put(credentials,a.get('credentialRef') if a else None) if credentials else a['credentialRef']
            if not a:a={'id':str(uuid.uuid4()),'ceiling':{}};s['accounts'].append(a)  # the workspace ceiling applies until the user sets a lower one
            a.update(name=str(b.get('name',kind))[:100],venue='kalshi',environment=environment,connectionType=kind,subaccount=subaccount,liveEnabled=environment=='live',credentialRef=reference,status='disconnected',stale=True,error=None)
            if credentials:a['credentialMasks']=masks(credentials)
            a.pop('balance',None);event(s,'Kalshi connection saved.');return {'accountId':a['id']}
        elif action=='account-test':
            a=next(x for x in s['accounts'] if x['id']==b['id'])
            if a.get('status')=='removed':raise ValueError('This Kalshi connection was removed.')
            if not a.get('credentialRef'):raise ValueError('This account has no saved credentials. Add keys under Settings / rotate keys.')
            self.refresh(s,a,test=True)
        elif action=='account-disconnect':
            a=next(x for x in s['accounts'] if x['id']==b['id'])
            if a.get('status')=='removed':raise ValueError('This Kalshi connection was removed.')
            if any(o.get('committed') and o['accountId']==a['id'] and o['status'] in ('submitted','open','submission unknown') for o in s['orders']):raise ValueError('Reconcile outstanding submissions at the venue before disconnecting.')
            self.vault.delete(a['credentialRef']);a.update(status='disconnected',stale=True);a.pop('credentialRef',None)
            for agent in s['agentConfigs']:
                if agent['accountId']==a['id']:agent['status']='paused'
        elif action=='account-remove':
            a=next(x for x in s['accounts'] if x['id']==b['id'])
            if a.get('status')=='removed':raise ValueError('This Kalshi connection was removed.')
            if a.get('credentialRef'):raise ValueError('Disconnect this account before removing it.')
            if any(g['accountId']==a['id'] and g['status']!='deleted' for g in s['agentConfigs']):raise ValueError('Delete the agents that use this account first.')
            if any(o.get('committed') and o['accountId']==a['id'] and o['status'] in ('submitted','open','submission unknown') for o in s['orders']):raise ValueError('Reconcile outstanding submissions at the venue before removing this account.')
            a['status']='removed';event(s,'Kalshi connection removed.')
        elif action=='agent-save':
            a=next(x for x in s['accounts'] if x['id']==b['accountId'])
            if a.get('status')=='removed':raise ValueError('This Kalshi connection was removed.')
            if a['venue']!='kalshi' or a['status']!='connected':raise ValueError('Choose a connected Kalshi Demo or Live account.')
            if not isinstance(b.get('name'),str) or not b['name'].strip() or len(b['name'])>100:raise ValueError('Enter an agent name of 1–100 characters.')
            agent=next((x for x in s['agentConfigs'] if x['id']==b.get('id')),None)
            if agent and agent['status']=='deleted':raise ValueError('Deleted agents cannot be edited.')
            if agent and agent['accountId']!=a['id']:raise ValueError('Create a new agent to change its bound account.')
            look=validate_look(b['look']) if b.get('look') is not None else None  # checked before anything is stored
            if b.get('provider')==FREE_PROVIDER:
                # The free agent runs on the server's Gemini key: nothing is stored for this agent.
                validate_free_agent(b.get('model'))
                if agent and agent.get('modelCredentialRef'):self.vault.delete(agent.pop('modelCredentialRef'))
                if not agent:
                    agent={'id':str(uuid.uuid4()),'status':'draft','mandate':None,'personality':None};s['agentConfigs'].append(agent)
                agent.update(name=b['name'],accountId=a['id'],provider=FREE_PROVIDER,model=b['model'],modelValidated=True,modelKeyMask=None)
            else:
                key=b.get('modelKey') or (self.vault.get(agent['modelCredentialRef'])['api_key'] if agent and agent.get('modelCredentialRef') else '')
                validate_model_key(b['provider'],b['model'],key)
                reference=self.vault.put({'api_key':key},agent.get('modelCredentialRef') if agent else None)
                if not agent:
                    agent={'id':str(uuid.uuid4()),'status':'draft','mandate':None,'personality':None};s['agentConfigs'].append(agent)
                agent.update(name=b['name'],accountId=a['id'],provider=b['provider'],model=b['model'],modelValidated=True,modelCredentialRef=reference,modelKeyMask=masks({'key':key})['key'])
            if look:agent['look']=look
            s['chats'].setdefault(agent['id'],[]);event(s,'Agent saved; a user mandate is required before trading.');return {'agentId':agent['id']}
        elif action=='agent-look':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['id'])
            if agent['status']=='deleted':raise ValueError('Deleted agents cannot be edited.')
            agent['look']=validate_look(b.get('look'))
        elif action=='mandate-save':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['id'])
            if agent['status']=='deleted' or not agent.get('modelValidated'):raise ValueError('Validate this agent’s provider key before mandate setup.')
            if not trading_rules.ready(s):raise ValueError('Set your trading rules before an agent mandate.')
            a=next(x for x in s['accounts'] if x['id']==agent['accountId']);values=trading_rules.validate_mandate(b.get('mandate'),ceiling(s,a)['dailyPercent'])
            agent.update(personality=None,mandate=values)  # shares of the account's value; the form starts at presets
            if agent['status']=='draft':agent['status']='active' if s['permissions']['automation'] else 'paused'
            event(s,'User saved the agent mandate; future proposals use the current mandate.');return {'agentId':agent['id']}
        elif action=='agent-status':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['id'])
            if b['status'] not in ('active','paused'):raise ValueError('Invalid status.')
            if b['status']=='active' and (not agent.get('mandate') or not agent.get('modelValidated') or not s['permissions']['automation']):raise ValueError('Save a validated mandate and enable the account ceiling before resuming.')
            if agent['status']=='deleted':raise ValueError('Deleted agents cannot resume.')
            if b['status']=='active' and not trading_rules.ready(s):raise ValueError('Set your trading rules before resuming an agent.')
            account=next((a for a in s['accounts'] if a['id']==agent['accountId']),None)
            if b['status']=='active' and (not account or not account.get('credentialRef') or account.get('status')!='connected'):raise ValueError("Reconnect and successfully test this agent's Kalshi account before resuming.")
            agent['status']=b['status']
        elif action=='agent-delete':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['id']);agent['status']='deleted'
            if agent.get('modelCredentialRef'):self.vault.delete(agent.pop('modelCredentialRef'))  # free agents have none
        elif action=='permissions':
            scope=b.get('scope','defaults')
            if scope=='defaults':
                values=validate_permissions(b['values'])
                if 'dailyCap' in values:raise ValueError('Set the daily spending ceiling under Trading Rules.')
                s['permissions'].update(values)
            else:
                # One account's own, lower daily ceiling, as a share of its value; null removes it.
                a=next(x for x in s['accounts'] if x['id']==scope);values=b.get('values')
                if not isinstance(values,dict) or set(values)!={'dailyPercent'}:raise ValueError('Set the account ceiling as a percentage (dailyPercent).')
                pct=values['dailyPercent']
                if pct is None:(a.get('ceiling') or {}).pop('dailyPercent',None)
                elif isinstance(pct,bool) or not isinstance(pct,(int,float)) or not math.isfinite(pct) or not 0<=pct<=100:raise ValueError('The account ceiling must be between 0% and 100%.')
                else:a.setdefault('ceiling',{})['dailyPercent']=pct
            if s['permissions']['automation'] is False:
                for agent in s['agentConfigs']:
                    if agent['status']=='active':agent['status']='paused'
            event(s,'Account safety ceiling saved. Every proposal is rechecked against it.')
        elif action=='rules-save':
            # The user's own trading rules: every rule, no presets. Each save is a new version the governor records.
            changed=trading_rules.save(s,b.get('values'),now())
            if s['rules']['version']==1:event(s,'Trading rules set. Agents can trade within them.','RULES')
            elif changed:event(s,f"Trading rules updated (version {s['rules']['version']}): {', '.join(changed)}.",'RULES')
            return {'version':s['rules']['version']}
        elif action=='decision':
            o=next(x for x in s['orders'] if x['id']==b['id'])
            if o['status']!='awaiting approval':return o
            approve_by=(o.get('governedTerms') or {}).get('approveBy')
            if b['approve'] is True and approve_by and datetime.datetime.fromisoformat(now())>datetime.datetime.fromisoformat(approve_by):
                # The governor's sizing is only valid for a limited time; a late approval must not trade on it.
                o['status']='cancelled';event(s,'Approval window expired; the governor sized this trade for earlier market conditions.','EXPIRED',o['id']);return o
            if b['approve'] is True:return self.evaluate(s,o,True)
            o['status']='denied';event(s,'User denied exact proposal.','DENIED',o['id'])
        else:raise ValueError('Unsupported user action.')  # chat runs in agent_chat.py, outside the workspace lock
    def public(self,s):
        from governor_service import GovernorService
        governor = GovernorService(self)
        summaries = {a['id']: governor.account_summary(s, a) for a in s['accounts']
                     if a['venue'] == 'kalshi' and a.get('status') != 'removed'}
        order_governors = governor.order_governors(s)
        # Only compact allowlisted governor details reach the browser. Orders and events grow without limit, so the
        # state carries a bounded window of them (order_views.py); the rest is read page by page.
        public=copy.deepcopy({k:v for k,v in s.items() if not k.startswith('governor') and k not in ('chatRequests','keys','orders','events')})  # request IDs and idempotency keys are server bookkeeping
        for a in public['accounts']:
            a['hasCredentials']=bool(a.get('credentialRef'));a.pop('credentialRef',None);a.pop('permissions',None);a['effectiveCeiling']=ceiling(s,a)
            a['connectionType']='kalshi-live' if a['environment']=='live' else 'kalshi-demo'
        activity=order_views.agent_activity(s['orders'])
        for a in public['agentConfigs']:
            a.pop('modelCredentialRef',None);a['usage']=__import__('engine').usage(s,a['accountId'],a['id'])
            a['activity']=activity.get(a['id'],{'awaiting':0,'lastOrder':None})
        for a in public['accounts']:
            a['summary'] = summaries.get(a['id'])
        public.update(order_views.window(s, order_governors))
        totals = [summaries[a['id']] for a in s['accounts'] if a['id'] in summaries
                  and a.get('status') != 'disconnected']
        public['summary'] = {key: sum(t[key] for t in totals)
                             if totals and all(t[key] is not None for t in totals) else None
                             for key in ('accountValue', 'spentToday', 'dailyCap', 'atRisk', 'exposureCap', 'lossHeadroom')}
        public['summary'].update(rulesVersion=s.get('rules', {}).get('version'),
                                 breakerLatched=any(t['breakerLatched'] for t in totals),
                                 stale=any(t['stale'] for t in totals))
        public['providers']=catalog();public['rulesCatalog']=trading_rules.catalog()
        if FREE_PROVIDER in public['providers']:
            # The server's own count for its own UTC day, so the browser's clock never decides what is left.
            day=datetime.datetime.now(datetime.timezone.utc).date().isoformat();usage=s.get('freeAgentUsage') or {}
            used=usage.get('messages',0) if usage.get('day')==day else 0;limit=public['providers'][FREE_PROVIDER]['dailyMessages']
            public['freeAgentQuota']={'day':day,'used':used,'limit':limit,'left':max(0,limit-used)}
        public['accounts']=[a for a in public['accounts'] if a['venue']=='kalshi' and a.get('status')!='removed']
        visible={a['id'] for a in public['accounts']}
        public['agentConfigs']=[a for a in public['agentConfigs'] if a['accountId'] in visible]
        return public
