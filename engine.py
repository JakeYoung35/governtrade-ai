import copy, datetime as dt, uuid, re
from adapters import BROKER, MARKET_DATA
import trading_rules
AGENTS=[('crypto','Crypto Markets Agent','BTC & ETH · 15-minute contracts · Spot crypto'),('sports','Sports Markets Agent','Game winners · Tournaments · Season outcomes'),('politics','Politics & Elections Agent','Elections · Political events'),('economics','Economics & Finance Agent','Inflation · Rates · Economic releases'),('culture','Culture & Entertainment Agent','Awards · Releases · Cultural events')]
def uid(): return str(uuid.uuid4())[:12]
def stamp(s): return s['clock']
def markets(clock):
    date=clock[:10]; result=[]
    for a,n,d in AGENTS:
        names={'crypto':['BTC 15-minute Up/Down','ETH 15-minute Up/Down','BTC Spot'], 'sports':['Atlanta vs Boston — game winner','Championship — tournament winner','Season — division winner'], 'politics':['Election — candidate wins','Policy bill — passes','Election — turnout exceeds 60%'], 'economics':['Inflation — below 3%','Rates — next decision cut','GDP — above 2%'], 'culture':['Awards — best picture winner','Release — opening exceeds forecast','Music — album reaches number one']}[a]
        for i,name in enumerate(names): result.append({'id':f'{a}-{i}-{date}','agent':a,'name':name,'instrument':'spot' if name=='BTC Spot' else 'prediction','price':50000 if name=='BTC Spot' else [.5,.4,.6][i],'venue':'Demo Exchange','quoteAt':clock,'closeAt':(dt.datetime.fromisoformat(clock)+dt.timedelta(minutes=15 if a=='crypto' and i<2 else 1440)).isoformat(),'slippage':0.2})
    return result
DEFAULT={'autoLimit':25,'dailyCap':100,'dailyLossCap':50,'marketCap':50,'exposureCap':500,'minimum':100,'slippage':1,'automation':True,'alwaysApprove':False,'newMarketApproval':False,'notifications':True,'agents':[a[0] for a in AGENTS],'categories':[a[0] for a in AGENTS],'venues':['Demo Exchange'],'approvedMarkets':[]}
def seed():
    clock='2026-10-09T09:00:00';return {'mode':'demo','demoAgents':demo_agents(),'clock':clock,'cash':1000.,'deposits':1000.,'reserved':0.,'realized':0.,'permissions':{'dailyCap':500,'automation':True},'safety':copy.deepcopy(SAFETY),'disabled':[],'orders':[],'positions':[],'chats':{a[0]:[] for a in AGENTS},'events':[],'keys':{},'markets':markets(clock)}
def event(s,text,kind='UPDATED',trade=None):s['events'].insert(0,{'id':uid(),'time':stamp(s),'text':text,'kind':kind,'trade':trade})
def exposure(s):return round(sum(p['cost'] for p in s['positions'] if p['status']=='open'),2)
def checks(s,o,approved=False):return layered_checks(s,o,approved)
def evaluate(s,o,approved=False):
    e=checks(s,o,approved);o['evaluations'].append(e);o['decision']=e['outcome'];o['status']={'BLOCK':'blocked','REQUIRE_APPROVAL':'awaiting approval','ALLOW':'submitted'}[e['outcome']]
    event(s,f"{o['market']['name']} · ${o['amount']:.2f} · {e['outcome']}",e['outcome'],o['id'])
    if e['outcome']=='ALLOW':
        # Atomic simulated broker: reserve, submit, then immediately fill exact order.
        s['reserved']+=o['amount'];o['status']='open'; price=MARKET_DATA.quote(o['market'])['price'];price=1-price if o['direction']=='No' else price
        fill=BROKER.fill(o['amount'],price);fee=fill['fee'];principal=fill['principal'];qty=fill['quantity'];s['cash']=round(s['cash']-o['amount'],2);s['reserved']=round(s['reserved']-o['amount'],2);o.update(status='filled',filled=True,fee=fee,quantity=qty,fillPrice=price,automatic=not approved)
        s['positions'].insert(0,{'id':uid(),'order':o['id'],'market':copy.deepcopy(o['market']),'direction':o['direction'],'quantity':qty,'price':price,'cost':o['amount'],'value':principal,'status':'open','pnl':-fee})
        event(s,'Simulated fill recorded; cash and position updated.','FILLED',o['id'])
    return o

def propose(s,agent,market,amount,direction,request,key):
    if key in s['keys']:return next(x for x in s['orders'] if x['id']==s['keys'][key])
    if not isinstance(amount,(float,int)) or not 0<amount<=1000000:raise ValueError('Enter a valid positive amount.')
    o={'id':uid(),'agent':agent,'accountId':'demo-account','market':copy.deepcopy(market),'amount':round(amount,2),'direction':direction,'request':request,'time':stamp(s),'status':'proposed','evaluations':[],'filled':False};s['orders'].insert(0,o);s['keys'][key]=o['id'];return evaluate(s,o)
def advance(s,hours):
    s['clock']=(dt.datetime.fromisoformat(s['clock'])+dt.timedelta(hours=hours)).isoformat()
    for pos in s['positions']:
        if pos['status']=='open' and pos['market']['instrument']=='prediction' and s['clock']>=pos['market']['closeAt']:
            # Deterministic simulated settlement, never a forecast.
            settlement=BROKER.settlement(pos['market']['id'],pos['direction'],pos['quantity']);win=settlement['won'];payout=settlement['payout'];pos.update(status='settled',value=payout,pnl=round(payout-pos['cost'],2),settlement={'time':s['clock'],'won':win,'payout':payout});s['cash']=round(s['cash']+payout,2);s['realized']=round(s['realized']+pos['pnl'],2);next(x for x in s['orders'] if x['id']==pos['order'])['status']='settled';event(s,f"Simulated settlement: {pos['market']['name']} · payout ${payout:.2f}",'SETTLED',pos['order'])
    s['markets']=markets(s['clock'])
    event(s,f"Simulation advanced {hours} hours. Quotes and simulated settlements updated.")
def action(s,b):
    a=b['action'];agent=b.get('agent','crypto')
    if a=='deposit':
        n=round(float(b['amount']),2)
        if not 0<n<=1000000:raise ValueError('Deposit must be between $0.01 and $1,000,000.')
        if b['key'] not in s['keys']:s['cash']=round(s['cash']+n,2);s['deposits']+=n;s['keys'][b['key']]='deposit';event(s,f'Added ${n:.2f} simulated funds.')
    elif a=='permissions':
        s['permissions'].update(validate_permissions(b['values']));event(s,'Account ceiling updated.')
    elif a=='toggle-agent':
        x=next(x for x in s['demoAgents'] if x['id']==agent);x['status']='paused' if x['status']=='active' else 'active'
    elif a=='preview':
        market=next(x for x in s['markets'] if x['id']==b['market'])
        return checks(s,{'market':market,'amount':float(b['amount']),'agent':agent,'direction':b['direction']})
    elif a=='trade':
        market=next(x for x in s['markets'] if x['id']==b['market'])
        if agent not in [a['id'] for a in s['demoAgents']]:raise ValueError('Select a demo agent.')
        return propose(s,agent,market,float(b['amount']),b['direction'],b.get('request','Structured trade'),b['key'])
    elif a=='decision':
        o=next(x for x in s['orders'] if x['id']==b['id'])
        if o['status']!='awaiting approval':return o
        if b['approve']:return evaluate(s,o,True)
        o['status']='denied';event(s,'User denied exact trade. No funds moved.','DENIED',o['id'])
    elif a=='advance':
        h=float(b['hours'])
        if not 0<h<=120:raise ValueError('Advance between 0 and 120 hours.')
        advance(s,h)
    else:raise ValueError('Unknown action.')

# User mandates and account ceilings are distinct policy layers.
PERSONALITIES={
 'Conservative':{'autoLimit':10,'dailyCap':50,'minimum':200,'newMarketApproval':False},
 'Moderate':{'autoLimit':25,'dailyCap':100,'minimum':100,'newMarketApproval':False},
 'Aggressive':{'autoLimit':50,'dailyCap':100,'minimum':50,'newMarketApproval':False}}
# Fixed limits of the simulated presenter demo and of schema-2 migration only. A real workspace's limits are the
# user's own trading rules (trading_rules.py); there are no presets.
MAIN_DEFAULT={'dailyCap':100,'automation':True}
SAFETY={'dailyLossCap':50,'marketCap':50,'exposureCap':500,'slippage':1}
CATEGORIES=['crypto','sports','economics','politics','culture','other']
DEMO_AGENTS=[
 {'id':'demo-conservative','name':'Economics Scout','personality':'Conservative','mandate':copy.deepcopy(PERSONALITIES['Conservative']),'demoMarket':'economics','priorDailySpend':0},
 {'id':'demo-moderate','name':'Sports Analyst','personality':'Moderate','mandate':copy.deepcopy(PERSONALITIES['Moderate']),'demoMarket':'sports','priorDailySpend':0},
 {'id':'demo-aggressive','name':'Momentum Trader','personality':'Aggressive','mandate':copy.deepcopy(PERSONALITIES['Aggressive']),'demoMarket':'crypto','priorDailySpend':80},
 {'id':'demo-new-market','name':'Discovery Agent','personality':'Moderate','mandate':{**PERSONALITIES['Moderate'],'newMarketApproval':True},'demoMarket':'politics','priorDailySpend':0}]
# Governor records (see governor/records.py): loss-breaker state, batch inputs, decisions, forecasts.
def governor_seed():return {'governor':{'breakers':{}},'governorBatches':[],'governorDecisions':[],'governorForecasts':[],'governorBreakerEvents':[],'governorReplacedForecasts':[],'governorRequests':[]}
def main_seed():
 return {'schemaVersion':3,'mode':'main','clock':dt.datetime.now(dt.timezone.utc).isoformat(),'accounts':[],'agentConfigs':[],'permissions':{'automation':True},'rules':trading_rules.unset(),'orders':[],'events':[],'keys':{},'chats':{},'positions':[],**governor_seed()}
def migrate(s):
 if s.get('mode')!='main':return s
 for k,v in governor_seed().items():s.setdefault(k,v)
 if s.get('schemaVersion')==2:
  old=s['permissions'];s['safety']={k:old.get(k,v) for k,v in SAFETY.items()};s['permissions']={k:old.get(k,v) for k,v in MAIN_DEFAULT.items()}
  for a in s['accounts']:
   a['ceiling']={'dailyCap':min(a.get('permissions',{}).get('dailyCap',s['permissions']['dailyCap']),s['permissions']['dailyCap'])};a.pop('permissions',None)
   if a['venue']!='kalshi':a.update(status='disconnected',stale=True,error='Venue no longer supported.')
  for a in s['agentConfigs']:
   a.pop('category',None);a.pop('permissions',None);a.update(mandate=None,personality=None,status='draft',modelValidated=False)
  s['schemaVersion']=3
 return trading_rules.migrate(s)  # a workspace from before user-set rules must set them before trading

def workspace_ceiling(s):
 # The user's daily spending ceiling; 0 until trading rules are set. The demo keeps its scripted ceiling.
 if s.get('mode')!='main':return s['permissions']['dailyCap']
 return trading_rules.values(s)['dailySpendingCeiling'] if trading_rules.ready(s) else 0
def ceiling(s,account):
 top=workspace_ceiling(s)
 return {'dailyCap':min(top,account.get('ceiling',{}).get('dailyCap',top)),'automation':s['permissions']['automation']}
def rules(s,agent):return copy.deepcopy(agent.get('mandate') or {})
def usage(s,account_id,agent_id=None,market_id=None):
 orders=[o for o in s['orders'] if o.get('accountId')==account_id and (not agent_id or o['agent']==agent_id) and (o.get('committed') or o.get('filled'))]
 prior=next((a.get('priorDailySpend',0) for a in s.get('demoAgents',[]) if a['id']==agent_id),0)
 proposals=[o for o in s['orders'] if o.get('accountId')==account_id and (not agent_id or o['agent']==agent_id)]
 return {'lastAmount':proposals[-1]['amount'] if proposals else None,'daily':prior+sum(o['amount'] for o in orders if o['time'][:10]==s['clock'][:10]),'market':sum(o['amount'] for o in orders if not market_id or o['market']['id']==market_id),'held':sum(o['amount'] for o in orders if o['status'] in ('submission unknown','submitted','open'))}
def validate_permissions(values):
 import math
 if not isinstance(values,dict) or set(values)-set(MAIN_DEFAULT):raise ValueError('Only total daily spending and the account kill switch are configurable here.')
 for k,v in values.items():
  if k=='dailyCap' and (type(v) not in (int,float) or not math.isfinite(v) or v<0):raise ValueError('Daily ceiling must be a finite nonnegative amount.')
  if k=='automation' and type(v)!=bool:raise ValueError('Invalid kill switch setting.')
 return copy.deepcopy(values)
def validate_mandate(values,limit):
 import math
 if not isinstance(values,dict) or set(values)!={'autoLimit','dailyCap','minimum','newMarketApproval'}:raise ValueError('Complete the four mandate restrictions.')
 for k in ('autoLimit','dailyCap','minimum'):
  if type(values[k]) not in (float,int) or not math.isfinite(values[k]) or values[k]<0:raise ValueError('Mandate limits must be finite nonnegative amounts.')
 if type(values['newMarketApproval'])!=bool:raise ValueError('Choose whether new markets require approval.')
 if values['dailyCap']>limit or values['autoLimit']>values['dailyCap']:raise ValueError('Mandate spending cannot exceed the account ceiling; the automatic limit cannot exceed its daily cap.')
 return copy.deepcopy(values)
def result(rows,s):
 decision='BLOCK' if any(r['outcome']=='BLOCK' for r in rows) else 'REQUIRE_APPROVAL' if any(r['outcome']=='REQUIRE_APPROVAL' for r in rows) else 'ALLOW'
 layers={}
 for layer in ('account_ceiling','agent_mandate','execution'):
  relevant=[r for r in rows if r['layer']==layer];layers[layer]='BLOCK' if any(r['outcome']=='BLOCK' for r in relevant) else 'REQUIRE_APPROVAL' if any(r['outcome']=='REQUIRE_APPROVAL' for r in relevant) else 'ALLOW'
 return {'time':stamp(s),'outcome':decision,'checks':rows,'layers':layers}
def main_checks(s,o,approved=False):
 return layered_checks(s,o,approved)
def layered_checks(s,o,approved=False):
 demo=s.get('mode')!='main';agents=s.get('demoAgents',[]) if demo else s['agentConfigs'];agent=next(x for x in agents if x['id']==o['agent']);m=o['market'];amount=o['amount'];rows=[]
 account={'id':'demo-account','venue':'Demo Exchange','environment':'sandbox','status':'connected'} if demo else next(x for x in s['accounts'] if x['id']==agent['accountId'])
 b={'cash':s['cash']-s['reserved'],'reserved':s['reserved'],'exposure':exposure(s),'dailyLoss':max(0,-s['realized'])} if demo else o.get('_balance')
 policy=agent.get('mandate');p=policy or {'autoLimit':0,'dailyCap':0,'minimum':0,'newMarketApproval':True};cap=ceiling(s,account)
 # The user's dollar limits and price freshness (the demo keeps its scripted limits). No rules, no trading.
 ready=demo or trading_rules.ready(s);v=trading_rules.values(s) if not demo and ready else {}
 safety=s.get('safety',SAFETY) if demo else {'marketCap':v.get('marketDollarLimit',0),'exposureCap':v.get('exposureDollarLimit',0),'dailyLossCap':v.get('dailyLossDollarLimit',0)}
 quote_age=300 if demo else v.get('quoteMaxAgeSeconds',0)
 def c(name,ok,reason,layer='execution',approval=False):rows.append({'name':name,'outcome':'ALLOW' if ok else 'REQUIRE_APPROVAL' if approval else 'BLOCK','reason':reason,'layer':layer})
 c('Trading rules set',ready,'The account owner must set every trading rule before any agent trades.','account_ceiling')
 c('Mandate saved',bool(policy),'The user must save a mandate before this agent trades.','agent_mandate')
 c('Agent enabled',agent.get('status','active')=='active','Paused, draft and deleted agents cannot trade.','agent_mandate')
 c('Provider validated',demo or agent.get('modelValidated',False),'Provider key and selected model must be validated.','agent_mandate')
 c('Account kill switch',cap['automation'],'The account kill switch stops every agent.','account_ceiling')
 au=usage(s,account['id'],agent['id']);u=usage(s,account['id']);total=u['daily']+sum(x.get('priorDailySpend',0) for x in s.get('demoAgents',[])) if demo else max(u['daily'],(b or {}).get('externalDailySpend',0))+u['held']
 c('Total daily spending',total+amount<=cap['dailyCap'],f"${total:.2f} used; account ceiling ${cap['dailyCap']:.2f}.",'account_ceiling')
 if not demo:
  global_spend=sum(x['amount'] for x in s['orders'] if x.get('committed') and x['time'][:10]==s['clock'][:10])
  c('Workspace daily ceiling',global_spend+amount<=workspace_ceiling(s),'Connected accounts also share the workspace ceiling.','account_ceiling')
 c('Agent daily spending',au['daily']+amount<=p['dailyCap'],f"${au['daily']:.2f} used; this agent cap ${p['dailyCap']:.2f}.",'agent_mandate')
 cash=b['cash'] if b else 0;held=u['held']
 c('Fresh account balance',b is not None and account['status']=='connected','A fresh venue balance is required.')
 c('Available cash',cash-held>=amount,'Available cash excludes venue reservations; unresolved submissions are additionally held.')
 c('Minimum cash reserve',cash-held-amount>=p['minimum'],f"Keep ${p['minimum']:.2f} available for this agent.",'agent_mandate')
 c('Approved venue',account['venue']==m['venue'],'The proposal uses its saved Kalshi account.')
 used_markets={x['market']['id'] for x in s['orders'] if x['agent']==agent['id'] and x.get('filled')}
 c('Automatic authorization',approved or amount<=p['autoLimit'],f"Above ${p['autoLimit']:.2f} requires approval.",'agent_mandate',True)
 c('New market authorization',approved or not p['newMarketApproval'] or m['id'] in used_markets,'This agent must receive approval for a market it has not traded before.','agent_mandate',True)
 # Retain existing execution guardrails; approvals cannot remove any hard restriction.
 mu=sum(x['amount'] for x in s['orders'] if (x.get('committed') or x.get('filled')) and x['market']['id']==m['id'] and x['market']['venue']==m['venue'])
 c('Cumulative market budget',mu+amount<=safety['marketCap'],f"Your per-market dollar limit is ${safety['marketCap']:.2f}.")
 c('Open exposure',b is not None and b['exposure']+held+amount<=safety['exposureCap'],f"Open exposure and unresolved submissions must fit your ${safety['exposureCap']:.2f} limit.")
 c('Daily loss',b is not None and b['dailyLoss']<=safety['dailyLossCap'],f"Trading stops once today's loss reaches your ${safety['dailyLossCap']:.2f} limit.")
 age=(dt.datetime.fromisoformat(s['clock'])-dt.datetime.fromisoformat(m['quoteAt'])).total_seconds()
 c('Fresh quote',0<=age<=quote_age,f"Prices older than {quote_age} seconds cannot execute.")
 c('Market open',m.get('open',True) and s['clock']<m['closeAt'],'Market must still be open.')
 c('Valid order',amount>=1 and o['direction'] in ('Yes','No'),'Opening prediction orders require at least $1 and Yes/No.')
 c('Execution capability',demo or m.get('executionReady',False),m.get('executionNote','Demo simulated execution.'))
 c('Live confirmation',account['environment']=='sandbox' or account.get('liveEnabled',False),'Live accounts require explicit real-money confirmation.')
 return result(rows,s)

def demo_agents():return [{**copy.deepcopy(a),'accountId':'demo-account','status':'active','modelValidated':True} for a in DEMO_AGENTS]
