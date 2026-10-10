"""User management and the narrow, governed agent integration boundary."""
import copy, math, uuid
from adapters import adapter,ADAPTERS,VenueError,now
from engine import main_seed,checks,event,rules,validate_permissions,CATEGORIES
from vault import masks

class Workspace:
    def __init__(self,vault,persist=lambda s:None):self.vault=vault;self.persist=persist
    def venue(self,a):return adapter(a['venue'],self.vault.get(a['credentialRef']),a['environment'])
    def refresh(self,s,a):
        try:
            v=self.venue(a)
            if hasattr(v,'get_order') and a['environment']=='live':
                for o in s['orders']:
                    if o['accountId']==a['id'] and o['status']=='submitted' and o.get('venueOrderId'):
                        status=v.get_order(o['venueOrderId']);venue_status=status.get('status')
                        if venue_status=='FILLED':o.update(status='filled',filled=True,quantity=float(status.get('filled_size',0)),actualCost=float(status.get('filled_value',0))+float(status.get('total_fees',0)))
                        elif venue_status in ('CANCELLED','EXPIRED','FAILED'):o.update(status='cancelled',committed=False)
            b=v.get_balance_positions()
            if not all(math.isfinite(float(b[k])) and float(b[k])>=0 for k in ('cash','positionValue','exposure')):raise VenueError('Venue returned invalid balance values.')
            equity=b['cash']+b['positionValue'];day=now()[:10]
            if a.get('baseline',{}).get('day')!=day:a['baseline']={'day':day,'equity':equity}
            b['dailyLoss']=max(0,a['baseline']['equity']-equity);a.update(balance=b,status='connected',stale=False,error=None);return b
        except (ValueError,KeyError,ImportError):a.update(status='error',stale=True,error='Balance sync failed. Check credentials, environment, permissions and network.');return None
    def evaluate(self,s,o,approved=False):
        a=next(x for x in s['accounts'] if x['id']==o['accountId']);v=self.venue(a);s['clock']=now();b=self.refresh(s,a)
        try:m=v.get_quote(o['market']['id'],o['direction'])
        except ValueError:m={**o['market'],'open':False,'executionReady':False,'executionNote':'Fresh quote unavailable.'}
        # Approval authorizes this exact market/direction/budget. A price increase needs fresh approval.
        if approved and m['price']>o['market']['price']:
            o['priceChanged']=True;approved=False
        s['clock']=now();o['market']=m;o['_balance']=b;e=checks(s,o,approved);
        if o.pop('priceChanged',False) and e['outcome']=='ALLOW':
            e['checks'].append({'name':'Price changed','outcome':'REQUIRE_APPROVAL','reason':'Price increased; review the updated exact proposal.'});e['outcome']='REQUIRE_APPROVAL'
        o.pop('_balance',None);o['evaluations'].append(e);o['decision']=e['outcome'];o['status']={'ALLOW':'submitted','BLOCK':'blocked','REQUIRE_APPROVAL':'awaiting approval'}[e['outcome']]
        event(s,f"{m['name']} · ${o['amount']:.2f} · {e['outcome']}",e['outcome'],o['id'])
        if e['outcome']=='ALLOW':
            o['committed']=True;o['status']='submission unknown';self.persist(s) # crash-safe budget reservation before network request
            try:
                response=v.place_order(o)
                if not response.get('success',True):o.update(status='rejected',committed=False)
                else:
                    o['venueOrderId']=response.get('order_id') or response.get('success_response',{}).get('order_id');o['status']='submitted';o['automatic']=not approved
                    if 'fill_count' in response:
                        filled=float(response['fill_count']);o.update(status='filled' if filled else 'cancelled',filled=filled>0,quantity=filled)
                        if filled:
                            fill_price=float(response.get('average_fill_price',0));fill_price=1-fill_price if o['direction']=='No' else fill_price;cost=filled*(fill_price+float(response.get('average_fee_paid',0)));o['actualCost']=round(cost,2)
                        else:o['committed']=False
                    if a['venue']=='coinbase' and a['environment']=='sandbox':o.update(status='mock submitted',mock=True,committed=False)
                event(s,'Venue submission result recorded.','SUBMITTED',o['id'])
            except ValueError:event(s,'Submission result unknown; budget remains reserved. Reconcile with venue before retrying.','UNKNOWN',o['id'])
            self.refresh(s,a)
        return o
    def proposal(self,s,b):
        key=b.get('idempotencyKey','')
        if not isinstance(key,str) or not 1<=len(key)<=200:raise ValueError('An idempotencyKey is required.')
        agent=next(x for x in s['agentConfigs'] if x['id']==b['agentId']);scoped=agent['id']+':'+key
        if scoped in s['keys']:return next(x for x in s['orders'] if x['id']==s['keys'][scoped])
        amount=b['amount']
        if type(amount) not in (int,float) or not math.isfinite(amount) or not 1<=amount<=1000000:raise ValueError('Use a finite all-in USD budget between $1 and $1,000,000.')
        if not isinstance(b.get('marketId'),str) or not isinstance(b.get('reasoning'),str) or len(b['reasoning'])>8000:raise ValueError('marketId and agent reasoning are required.')
        a=next(x for x in s['accounts'] if x['id']==agent['accountId']);v=self.venue(a);m=v.get_quote(b['marketId'],b['direction']);o={'id':str(uuid.uuid4()),'agent':agent['id'],'accountId':a['id'],'market':m,'amount':round(amount,2),'direction':b['direction'],'request':b['reasoning'],'time':now(),'evaluations':[],'filled':False};s['orders'].insert(0,o);s['keys'][scoped]=o['id'];return self.evaluate(s,o)
    def action(self,s,b):
        action=b['action'];s['clock']=now()
        if action=='refresh':
            for a in s['accounts']:
                if a['status']!='disconnected':self.refresh(s,a)
        elif action=='account-save':
            venue=b['venue'];environment=b.get('environment','sandbox')
            if venue not in ADAPTERS or environment not in ('sandbox','live'):raise ValueError('Choose a registered venue and sandbox or live.')
            if environment=='live' and b.get('liveEnabled') is not True:raise ValueError('Explicitly enable live trading for this account.')
            a=next((a for a in s['accounts'] if a['id']==b.get('id')),None)
            credentials=b.get('credentials')
            if not a and not credentials:raise ValueError('Credentials are required.')
            if credentials:
                fields={'key_id','private_key'} if venue=='kalshi' else {'key_name','private_key'}
                if set(credentials)!=fields or any(not isinstance(v,str) or not v.strip() for v in credentials.values()):raise ValueError('Complete the required venue credential fields.')
                identifier='key_id' if venue=='kalshi' else 'key_name'
                for existing in s['accounts']:
                    if existing.get('credentialRef') and existing['id']!=b.get('id') and existing['venue']==venue and existing['environment']==environment and self.vault.get(existing['credentialRef']).get(identifier)==credentials[identifier]:raise ValueError('This API key is already connected in that environment.')
                # Validate key type even on an unauthenticated Coinbase sandbox.
                test=adapter(venue,credentials,'live');test.headers('GET',test.base+'/accounts')
            if a:
                if a['venue']!=venue:raise ValueError('Create a new connection to change venues.')
                if any(o.get('committed') and o['accountId']==a['id'] and o['status'] in ('submitted','open','submission unknown') for o in s['orders']):raise ValueError('Reconcile outstanding submissions before rotating or changing this account.')
            else:
                a={'id':str(uuid.uuid4()),'permissions':{}};s['accounts'].append(a)
            if credentials:a['credentialRef']=self.vault.put(credentials,a.get('credentialRef'));a['credentialMasks']=masks(credentials)
            if environment=='live' and b.get('liveEnabled') is not True:raise ValueError('Explicitly enable live trading for this account.')
            a.update(name=str(b.get('name',venue))[:100],venue=venue,environment=environment,liveEnabled=environment=='live',status='disconnected',stale=True,error=None)
            a.pop('balance',None);event(s,'Venue connection saved; test the connection before trading.')
        elif action=='account-test':
            a=next(x for x in s['accounts'] if x['id']==b['id']);self.refresh(s,a)
        elif action=='account-disconnect':
            a=next(x for x in s['accounts'] if x['id']==b['id'])
            if any(o.get('committed') and o['accountId']==a['id'] and o['status'] in ('submitted','open','submission unknown') for o in s['orders']):raise ValueError('Reconcile outstanding submissions at the venue before disconnecting.')
            self.vault.delete(a['credentialRef']);a.update(status='disconnected',stale=True);a.pop('credentialRef',None)
            for agent in s['agentConfigs']:
                if agent['accountId']==a['id']:agent['status']='paused'
        elif action=='agent-save':
            category=b['category'];a=next(x for x in s['accounts'] if x['id']==b['accountId'])
            if a['status']!='connected' or category not in ADAPTERS[a['venue']].categories:raise ValueError('Choose a connected compatible account.')
            if b['provider'] not in ('openai','anthropic','google','other') or not b['name'].strip() or not b['model'].strip():raise ValueError('Complete the agent name, provider and model.')
            agent=next((x for x in s['agentConfigs'] if x['id']==b.get('id')),None)
            if not agent:
                if not b.get('modelKey'):raise ValueError('Model API key is required.')
                agent={'id':str(uuid.uuid4()),'permissions':{},'status':'active'};s['agentConfigs'].append(agent)
            elif agent['accountId']!=a['id']:raise ValueError('Create a new agent to change its account; audit history keeps its original binding.')
            if b.get('modelKey'):agent['modelCredentialRef']=self.vault.put({'api_key':b['modelKey']},agent.get('modelCredentialRef'));agent['modelKeyMask']=masks({'key':b['modelKey']})['key']
            agent.update(name=b['name'][:100],category=category,accountId=a['id'],provider=b['provider'],model=b['model'][:100]);s['chats'].setdefault(agent['id'],[])
        elif action=='agent-status':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['id'])
            if b['status'] not in ('active','paused'):raise ValueError('Invalid status.')
            agent['status']=b['status']
        elif action=='agent-delete':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['id']);agent['status']='deleted';self.vault.delete(agent['modelCredentialRef']);agent.pop('modelCredentialRef',None)
        elif action=='permissions':
            values=validate_permissions(b['values']);scope=b.get('scope','defaults')
            if scope=='defaults':s['permissions'].update(values)
            else:
                obj=next(x for x in s['accounts']+s['agentConfigs'] if x['id']==scope);obj.setdefault('permissions',{}).update(values)
            event(s,'Trading permissions saved; next evaluation uses the current rules.')
        elif action=='decision':
            o=next(x for x in s['orders'] if x['id']==b['id'])
            if o['status']!='awaiting approval':return o
            if b['approve'] is True:return self.evaluate(s,o,True)
            o['status']='denied';event(s,'User denied exact proposal.','DENIED',o['id'])
        elif action=='chat':
            agent=next(x for x in s['agentConfigs'] if x['id']==b['agent']);s['chats'][agent['id']].append({'role':'customer','text':str(b['text'])[:8000]});s['chats'][agent['id']].append({'role':'agent','text':'Instruction saved for the agentic integration. No model was called and no trade was proposed. Your teammate’s worker will consume instructions and submit through the governed proposal API.'})
        else:raise ValueError('Unsupported user action.')
    def public(self,s):
        public=copy.deepcopy(s)
        for a in public['accounts']:a.pop('credentialRef',None)
        for a in public['agentConfigs']:a.pop('modelCredentialRef',None);a['effectiveRules']=rules(s,a);a['usage']=__import__('engine').usage(s,a['accountId'],a['id'])
        public['venues']=[{'id':k,'categories':v.categories} for k,v in ADAPTERS.items()];return public
