import copy, datetime as dt, uuid, re
from adapters import BROKER, MARKET_DATA
AGENTS=[('crypto','Crypto Markets Agent','BTC & ETH · 15-minute contracts · Spot crypto'),('sports','Sports Markets Agent','Game winners · Tournaments · Season outcomes'),('politics','Politics & Elections Agent','Elections · Political events'),('economics','Economics & Finance Agent','Inflation · Rates · Economic releases'),('culture','Culture & Entertainment Agent','Awards · Releases · Cultural events')]
def uid(): return str(uuid.uuid4())[:12]
def stamp(s): return s['clock']
def markets(clock):
    date=clock[:10]; result=[]
    for a,n,d in AGENTS:
        names={'crypto':['BTC 15-minute Up/Down','ETH 15-minute Up/Down','BTC Spot'], 'sports':['Atlanta vs Boston — game winner','Championship — tournament winner','Season — division winner'], 'politics':['Election — candidate wins','Policy bill — passes','Election — turnout exceeds 60%'], 'economics':['Inflation — below 3%','Rates — next decision cut','GDP — above 2%'], 'culture':['Awards — best picture winner','Release — opening exceeds forecast','Music — album reaches number one']}[a]
        for i,name in enumerate(names): result.append({'id':f'{a}-{i}-{date}','agent':a,'name':name,'instrument':'spot' if name=='BTC Spot' else 'prediction','price':50000 if name=='BTC Spot' else [.5,.4,.6][i],'venue':'Demo Exchange','quoteAt':clock,'closeAt':(dt.datetime.fromisoformat(clock)+dt.timedelta(minutes=15 if a=='crypto' and i<2 else 1440)).isoformat(),'slippage':0.2})
    return result
DEFAULT={'autoLimit':25,'dailyCap':100,'mandateCap':100,'marketCap':50,'exposureCap':500,'minimum':100,'slippage':1,'automation':True,'recurring':True,'alwaysApprove':False,'newMarketApproval':False,'notifications':True,'agents':[a[0] for a in AGENTS],'categories':[a[0] for a in AGENTS],'venues':['Demo Exchange'],'approvedMarkets':[]}
def seed():
    clock='2026-10-09T09:00:00';return {'clock':clock,'cash':1000.,'deposits':1000.,'reserved':0.,'realized':0.,'permissions':copy.deepcopy(DEFAULT),'disabled':[],'orders':[],'positions':[],'mandates':[],'chats':{a[0]:[] for a in AGENTS},'events':[],'keys':{},'markets':markets(clock)}
def event(s,text,kind='UPDATED',trade=None):s['events'].insert(0,{'id':uid(),'time':stamp(s),'text':text,'kind':kind,'trade':trade})
def exposure(s):return round(sum(p['cost'] for p in s['positions'] if p['status']=='open'),2)
def checks(s,o,approved=False):
    p=s['permissions']; amount=o['amount']; market=o['market']; m=next((x for x in s['mandates'] if x['id']==o.get('mandate')),None); rows=[]
    def c(name,ok,reason,approval=False):rows.append({'name':name,'outcome':'ALLOW' if ok else 'REQUIRE_APPROVAL' if approval else 'BLOCK','reason':reason})
    c('Available cash',amount<=s['cash']-s['reserved'],'Trade must fit unreserved cash.')
    c('Minimum cash reserve',s['cash']-s['reserved']-amount>=p['minimum'],f"Keep ${p['minimum']:.2f} available.")
    c('Agent enabled',p['automation'] and o['agent'] not in s['disabled'] and o['agent'] in p['agents'],'Automation and selected agent must be enabled.')
    c('Market category',market['agent'] in p['categories'],'Category must be permitted.')
    c('Approved venue',market['venue'] in p['venues'],'Venue must be permitted.')
    spent=sum(x['amount'] for x in s['orders'] if x.get('filled') and x['time'][:10]==s['clock'][:10])
    c('Shared daily budget',spent+amount<=p['dailyCap'],f"${spent:.2f} spent; ${p['dailyCap']:.2f} daily cap.")
    marketSpent=sum(x['amount'] for x in s['orders'] if x.get('filled') and x['market']['id']==market['id'])
    c('Cumulative market budget',marketSpent+amount<=p['marketCap'],f"${marketSpent:.2f} invested in this market; ${p['marketCap']:.2f} cap.")
    c('Open exposure',exposure(s)+amount<=p['exposureCap'],f"Maximum exposure ${p['exposureCap']:.2f}.")
    c('Market open',s['clock']<market['closeAt'],'Contract must still be open.')
    c('Fresh quote',(dt.datetime.fromisoformat(s['clock'])-dt.datetime.fromisoformat(market['quoteAt'])).total_seconds()<=300,'Quote expires after five simulated minutes; stale proposals require a new request.')
    c('Slippage',market['slippage']<=p['slippage'],f"Quoted slippage {market['slippage']}%; maximum {p['slippage']}%.")
    c('Valid order',amount>=1 and o['direction'] in (['Buy'] if market['instrument']=='spot' else ['Yes','No']),'Minimum $1; valid contract direction required.')
    if m:
        c('Active mandate',m['status']=='active' and m['start']<=s['clock']<m['end'] and p['recurring'],'Mandate must be active, unexpired, with recurring permission.')
        c('Mandate total budget',m['spent']+amount<=min(m['budget'],p['mandateCap']),'Fees count toward the lifetime mandate budget.')
        used=sum(x['amount'] for x in s['orders'] if x.get('filled') and x.get('mandate')==m['id'] and x['market']['id']==market['id'])
        c('Mandate market budget',used+amount<=m['perMarket'],'Cumulative per-market spending cannot be bypassed by splitting orders.')
    c('Automatic authorization',approved or (not p['alwaysApprove'] and amount<=p['autoLimit']),'Exact user approval required above automatic threshold or when every-trade approval is enabled.',True)
    c('New market authorization',approved or not p['newMarketApproval'] or market['name'] in p['approvedMarkets'],'New markets require transaction-specific approval.',True)
    outcome='BLOCK' if any(x['outcome']=='BLOCK' for x in rows) else 'REQUIRE_APPROVAL' if any(x['outcome']=='REQUIRE_APPROVAL' for x in rows) else 'ALLOW'
    return {'time':stamp(s),'outcome':outcome,'checks':rows}
def evaluate(s,o,approved=False):
    e=checks(s,o,approved);o['evaluations'].append(e);o['decision']=e['outcome'];o['status']={'BLOCK':'blocked','REQUIRE_APPROVAL':'awaiting approval','ALLOW':'submitted'}[e['outcome']]
    event(s,f"{o['market']['name']} · ${o['amount']:.2f} · {e['outcome']}",e['outcome'],o['id'])
    if e['outcome']=='ALLOW':
        # Atomic simulated broker: reserve, submit, then immediately fill exact order.
        s['reserved']+=o['amount'];o['status']='open'; price=MARKET_DATA.quote(o['market'])['price'];price=1-price if o['direction']=='No' else price
        fill=BROKER.fill(o['amount'],price);fee=fill['fee'];principal=fill['principal'];qty=fill['quantity'];s['cash']=round(s['cash']-o['amount'],2);s['reserved']=round(s['reserved']-o['amount'],2);o.update(status='filled',filled=True,fee=fee,quantity=qty,fillPrice=price,automatic=not approved)
        s['positions'].insert(0,{'id':uid(),'order':o['id'],'market':copy.deepcopy(o['market']),'direction':o['direction'],'quantity':qty,'price':price,'cost':o['amount'],'value':principal,'status':'open','pnl':-fee})
        if o.get('mandate'):
            m=next(x for x in s['mandates'] if x['id']==o['mandate']);m['spent']=round(m['spent']+o['amount'],2)
        event(s,'Simulated fill recorded; cash and position updated.','FILLED',o['id'])
    return o

def propose(s,agent,market,amount,direction,request,key,mandate=None):
    if key in s['keys']:return next(x for x in s['orders'] if x['id']==s['keys'][key])
    if not isinstance(amount,(float,int)) or not 0<amount<=1000000:raise ValueError('Enter a valid positive amount.')
    o={'id':uid(),'agent':agent,'market':copy.deepcopy(market),'amount':round(amount,2),'direction':direction,'request':request,'time':stamp(s),'mandate':mandate,'status':'proposed','evaluations':[],'filled':False};s['orders'].insert(0,o);s['keys'][key]=o['id'];return evaluate(s,o)
def advance(s,hours):
    s['clock']=(dt.datetime.fromisoformat(s['clock'])+dt.timedelta(hours=hours)).isoformat()
    for pos in s['positions']:
        if pos['status']=='open' and pos['market']['instrument']=='prediction' and s['clock']>=pos['market']['closeAt']:
            # Deterministic simulated settlement, never a forecast.
            settlement=BROKER.settlement(pos['market']['id'],pos['direction'],pos['quantity']);win=settlement['won'];payout=settlement['payout'];pos.update(status='settled',value=payout,pnl=round(payout-pos['cost'],2),settlement={'time':s['clock'],'won':win,'payout':payout});s['cash']=round(s['cash']+payout,2);s['realized']=round(s['realized']+pos['pnl'],2);next(x for x in s['orders'] if x['id']==pos['order'])['status']='settled';event(s,f"Simulated settlement: {pos['market']['name']} · payout ${payout:.2f}",'SETTLED',pos['order'])
    s['markets']=markets(s['clock'])
    for m in s['mandates']:
        if m['status']!='active':continue
        if s['clock']>=m['end']:m['status']='expired';event(s,'Mandate expired; future trading stopped.','EXPIRED');continue
        if s['clock']>=m['next']:
            for market in [x for x in s['markets'] if x['agent']==m['agent'] and x['instrument']=='prediction']:
                if m['budget']-m['spent']>=1:propose(s,m['agent'],market,min(m['perMarket'],m['budget']-m['spent']), 'Yes',m['request'],f"{m['id']}-{market['id']}-{s['clock']}",m['id'])
            m['next']=(dt.datetime.fromisoformat(s['clock'])+dt.timedelta(hours=m['frequency'])).isoformat()
    event(s,f"Simulation advanced {hours} hours. No background trading runs while the app is closed.")
def action(s,b):
    a=b['action'];agent=b.get('agent','crypto')
    if a=='deposit':
        n=round(float(b['amount']),2)
        if not 0<n<=1000000:raise ValueError('Deposit must be between $0.01 and $1,000,000.')
        if b['key'] not in s['keys']:s['cash']=round(s['cash']+n,2);s['deposits']+=n;s['keys'][b['key']]='deposit';event(s,f'Added ${n:.2f} simulated funds.')
    elif a=='permissions':
        p=copy.deepcopy(s['permissions'])
        for k,v in b['values'].items():
            if k not in DEFAULT:raise ValueError('Unknown permission.')
            if type(DEFAULT[k]) in (int,float):
                if type(v) not in (int,float) or v<0:raise ValueError('Limits must be nonnegative numbers.')
            elif type(v)!=type(DEFAULT[k]):raise ValueError('Invalid permission type.')
            p[k]=v
        s['permissions']=p;event(s,'Trading permissions updated; next execution uses current rules.')
    elif a=='toggle-agent':
        if agent in s['disabled']:s['disabled'].remove(agent)
        else:s['disabled'].append(agent)
        event(s,f'{agent} agent status changed.')
    elif a=='preview':
        market=next(x for x in s['markets'] if x['id']==b['market'])
        return checks(s,{'market':market,'amount':float(b['amount']),'agent':agent,'direction':b['direction']})
    elif a=='trade':
        market=next(x for x in s['markets'] if x['id']==b['market'])
        if market['agent']!=agent:raise ValueError('Choose a market supported by this agent.')
        return propose(s,agent,market,float(b['amount']),b['direction'],b.get('request','Structured trade'),b['key'])
    elif a=='chat':
        text=b['text'].strip();s['chats'][agent].append({'role':'customer','text':text});nums=re.findall(r'\$\s*(\d+(?:\.\d+)?)',text)
        if re.search(r'five days|5 days|per market|multiple',text,re.I):reply='Create a mandate below. Confirm the total budget, cumulative per-market cap, duration, frequency and selection criteria. No trades execute before confirmation.'
        elif nums:
            market=next((x for x in s['markets'] if x['agent']==agent and (('btc' in text.lower() and 'BTC' in x['name']) or ('eth' in text.lower() and 'ETH' in x['name']) or x['name'].lower() in text.lower()) and (('spot' in text.lower())==(x['instrument']=='spot'))),None)
            direction='Buy' if market and market['instrument']=='spot' else 'Yes' if re.search(r'\byes\b|\bup\b',text,re.I) else 'No' if re.search(r'\bno\b|\bdown\b',text,re.I) else None
            if market and direction:
                o=propose(s,agent,market,float(nums[0]),direction,text,b['key']);reply=f"Trade {o['id']}: {o['decision']} · {o['status']}. Open the trade details to review all policy checks."
            else:reply='Please specify the exact market and direction. For example: “Invest $10 in BTC 15-minute Up/Down, Yes.” Or complete the structured trade form below. A 15-minute contract is different from spot crypto.'
        else:reply='Please include a dollar amount, market and direction, or configure a scheduled mandate below.'
        s['chats'][agent].append({'role':'agent','text':reply})
    elif a=='mandate':
        budget=float(b['budget']);per=float(b['perMarket']);days=int(b['days']);freq=int(b['frequency'])
        if not 1<=budget<=s['permissions']['mandateCap'] or not 1<=per<=budget or not 1<=days<=30 or not 1<=freq<=120:raise ValueError('Check mandate budget, per-market cap, duration and frequency against your permissions.')
        m={'id':uid(),'agent':agent,'budget':budget,'perMarket':per,'spent':0,'start':stamp(s),'end':(dt.datetime.fromisoformat(s['clock'])+dt.timedelta(days=days)).isoformat(),'next':(dt.datetime.fromisoformat(s['clock'])+dt.timedelta(hours=freq)).isoformat(),'frequency':freq,'criteria':'Demo rotation: available prediction contracts, Yes, within governance limits','status':'active','request':b.get('request','Confirmed structured mandate')};s['mandates'].insert(0,m);event(s,'Confirmed mandate activated; every due proposal will be independently checked.')
    elif a=='mandate-status':
        m=next(x for x in s['mandates'] if x['id']==b['id']);status=b['status']
        if status not in ['paused','active','cancelled']:raise ValueError('Invalid status.')
        if m['status'] in ['expired','cancelled'] or s['clock']>=m['end']:raise ValueError('Expired or cancelled mandates cannot resume.')
        m['status']=status;event(s,f'Mandate {status}. Existing positions remain unchanged.')
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
