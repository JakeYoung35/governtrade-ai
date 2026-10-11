// Presentation-only onboarding. These values never reach account-save, agent-save, or a provider API.
const demoScene = {
  agentId: 'demo-conservative', category: 'economics', amount: 5,
  request: 'Invest $5 in Inflation — below 3%, Yes.',
  reason: 'The conservative agent proposes a small economics position within its $10 automatic limit and $50 daily cap.',
};
const demoAccountValues = {accountName:'My Kalshi Demo Account', kalshiKey:'DEMO-KALSHI-KEY-NOT-VALID', kalshiPrivate:'DEMO-PRIVATE-KEY'};
const demoAgentValues = {agentName:'Economics Scout', provider:'OpenAI', model:'GPT-5', agentKey:'sk-demo-not-a-real-key', account:'My Kalshi Demo Account'};
const demoSteps = [
  {title:'Connect your Kalshi account', description:'Watch the demo cursor enter an account name and sample credentials. This connection is entirely simulated.', next:'Create the OpenAI agent'},
  {title:'Create your trading agent', description:'Choose OpenAI and GPT-5, enter a sample agent API key and bind the agent to the simulated Kalshi account.', next:'Review the agent mandate'},
  {title:'Review the agent’s personality & mandate', description:'The user controls the mandate. This conservative agent can automatically trade up to $10, spend $50 daily and must retain $200 in cash.', next:'Type the request'},
  {title:'Type the customer request', description:'Give the agent a trading instruction. The agent can propose a trade, but cannot change its own mandate.', next:'Prepare the proposal'},
  {title:'Agent prepares a proposal', description:demoScene.reason, next:'Run independent checks'},
  {title:'Governance evaluates both layers', description:'Both the agent mandate and account safety ceiling must allow this exact trade.', next:'Review the result'},
  {title:'Review the outcome', description:'The $5 trade passed every restriction and filled in the simulated ledger. No real funds or APIs were used.'},
];
let demoTx=null, typed='', typing=false, transition=false, demoRun=0, demoActive='', demoMenu='', demoClick=false;
let demoAccount={}, demoAgent={}, demoAccountConnected=false, demoAgentConnected=false;

function stopDemoAnimation(){demoRun++;typing=false;transition=false;demoActive='';demoMenu='';demoClick=false;}
function demoSleep(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
function demoField(field,label,value,{secret=false,select=false,options=[]}={}){
  const active=demoActive===field;
  return `<div class="demo-field ${active?'demo-field-active':''} ${active&&demoClick?'demo-field-click':''}"><label>${esc(label)}${select?
    `<div class="demo-select" aria-label="${esc(label)}" data-demo-field="${field}"><span>${esc(value||'Choose '+label.toLowerCase())}</span><span>⌄</span></div>${demoMenu===field?`<div class="demo-option-list">${options.map(option=>`<div>${esc(option)}</div>`).join('')}</div>`:''}`:
    `<input aria-label="${esc(label)}" data-demo-field="${field}" type="${secret?'password':'text'}" value="${esc(value||'')}" placeholder="${secret?'Sample credentials only':esc(label)}" readonly tabindex="-1" autocomplete="off">`}</label>${active?`<div class="demo-pointer" aria-label="Illustrated demonstration cursor">${icon('pointer',28)}<span>Demo cursor</span><i></i></div>`:''}</div>`;
}
function demoSetupCard(kind){
  const account=kind==='account', values=account?demoAccount:demoAgent, connected=account?demoAccountConnected:demoAgentConnected;
  return `<section class="card demo-setup"><div class="section-title"><h3>${account?'Connect Kalshi Demo':'Create your trading agent'}</h3><span class="badge blue">SIMULATED</span></div>${account?
    `<div class="demo-venue-choice">${icon('wallet')} Kalshi Demo <span class="badge neutral">Virtual funds</span></div>${demoField('accountName','Account name',values.accountName)}${demoField('kalshiKey','Kalshi API key ID',values.kalshiKey)}${demoField('kalshiPrivate','Kalshi private key',values.kalshiPrivate,{secret:true})}`:
    `${demoField('agentName','Agent name',values.agentName)}${demoField('provider','LLM provider',values.provider,{select:true,options:['OpenAI']})}${demoField('model','Model',values.model,{select:true,options:['GPT-5']})}${demoField('agentKey','OpenAI API key',values.agentKey,{secret:true})}${demoField('account','Connected Kalshi account',values.account,{select:true,options:[demoAccountValues.accountName]})}`}
    <div class="demo-field demo-submit ${demoActive===(account?'connectAccount':'saveAgent')?'demo-field-active':''}"><button type="button" class="full ${connected?'demo-saved':''}" disabled>${connected?icon('check')+' '+(account?'Kalshi Demo connected':'Agent saved · mandate required'):(account?'Connect Kalshi Demo':'Save agent')}</button>${demoActive===(account?'connectAccount':'saveAgent')?`<div class="demo-pointer" aria-label="Illustrated demonstration cursor">${icon('pointer',28)}<span>Demo cursor</span><i></i></div>`:''}</div><small>Illustrated setup only. Sample keys are not valid, saved or sent to any provider.</small></section>`;
}
function demoView(){
  if(!state.demoAgents)return '<section class="card"><h2>Loading the simulated walkthrough…</h2></section>';
  const a=state.demoAgents.find(a=>a.id===demoScene.agentId),tx=state.orders.find(o=>o.id===demoTx),evaluation=tx?.evaluations.at(-1);
  const locked=busy||typing||transition, beat=demoSteps[Math.max(step,0)];
  return `<div class="demo-workspace"><div class="demo-heading"><div><span class="eyebrow">GOVERNTRADE AI · GUIDED DEMO · SIMULATED</span><h1>Connect. Create. Trade under your rules.</h1><p>One walkthrough, from Kalshi setup to an independently governed trade.</p></div><button class="secondary" data-exit-demo>Exit demo</button></div>
    <div class="scenario-intro"><div><span class="badge ${step>=5?'green':'blue'}">${step>=5?'AUTHORIZED':'SIMULATED WALKTHROUGH'}</span><h2>${esc(step<0?'Start with your Kalshi account':beat.title)}</h2><p>${esc(beat.description)}</p></div><div class="step-indicator"><strong>${step<0?'Ready':(step+1)+' / '+demoSteps.length}</strong><small>${typing?'Demo in progress':'Waits for you'}</small></div></div>
    <div class="presentation-split"><section class="card customer-stage"><div class="panel-label">${icon('wallet',20)}<div><strong>CUSTOMER TRADING</strong><small>Kalshi Demo · OpenAI / GPT-5</small></div><span class="badge blue">Customer view</span></div>
    ${step<=0?demoSetupCard('account'):''}${step===1?demoSetupCard('agent'):''}
    ${step>=1?`<div class="demo-setup-summary">${icon('check')}<div><strong>${esc(demoAccountValues.accountName)}</strong><small>Kalshi Demo · simulated connection</small></div><span class="badge green">Connected</span></div>`:''}
    ${step>=2?`<div class="demo-setup-summary">${icon('bot')}<div><strong>Economics Scout</strong><small>OpenAI · GPT-5 · sample API key ••••-key</small></div><span class="badge green">Configured</span></div><div class="tour-account"><div class="row"><small>Investment account · SIMULATED USD</small></div><h2>${money(state.cash)}</h2><small>Cash moves only after authorization.</small></div>${focus(`<section class="card"><h3>Agent mandate · ${esc(a.personality)}</h3>${mandateRows({...a,usage:{lastAmount:tx?.amount,daily:a.priorDailySpend+state.orders.filter(o=>o.agent===a.id&&o.filled).reduce((n,o)=>n+o.amount,0)}},{cash:state.cash})}<small>Only the user can change this mandate.</small></section>`,step===2,'The user sets the trading boundaries')}`:''}
    ${step>=3?focus(`<div class="tour-chat"><div class="row"><strong>${icon('bot')} ${esc(a.name)}</strong><span class="badge neutral">Proposal only</span></div><div class="typing-request">${esc(typed)}${typing?'<span class="typing-caret">|</span>':''}</div><div class="illustrated-send"><span>Customer instruction</span>${icon('send')}</div></div>`,step===3,'The customer controls the instruction'):''}
    ${step>=4?focus(`<div class="proposal tour-proposal"><span class="eyebrow">AI-GENERATED PROPOSAL · SIMULATED</span><h2>${money(demoScene.amount)}</h2><strong>${esc(state.markets.find(m=>m.agent===demoScene.category).name)} · Yes</strong><p><strong>Agent reasoning</strong><br>${esc(demoScene.reason)}</p></div>`,step===4,'The agent proposes; it cannot edit its mandate'):''}
    ${tx?`<div class="tour-history"><h3>Trade outcome</h3>${demoStatus(tx.status)}<p>${esc(tx.market.name)} · ${money(tx.amount)}</p></div>`:''}</section>
    <section class="card bank-stage"><div class="panel-label">${icon('shield',21)}<div><strong>INVESTMENT GOVERNANCE COCKPIT</strong><small>Independent backend enforcement</small></div><span class="badge blue">Admin view</span></div><div class="cockpit-metrics">${[['Requested',state.orders.length],['Executed',state.orders.filter(o=>o.filled).length],['Pending',state.orders.filter(o=>o.status==='awaiting approval').length],['Stopped',state.orders.filter(o=>['blocked','denied'].includes(o.status)).length]].map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('')}</div>
    <div class="cockpit-policy"><h3>Account ceiling · all agents</h3><div class="demo-customer-limit"><small>Total daily cap</small><strong>${money(state.permissions.dailyCap)}</strong><small>All-agent trading ${state.permissions.automation?'enabled':'paused'}</small></div></div>
    ${step<2?`<div class="notice"><strong>${demoAccountConnected?'Simulated Kalshi account connected':'Account setup comes first'}</strong><p>${demoAgentConnected?'Agent configuration complete. The user reviews its mandate next.':'No trade can start during this setup walkthrough.'}</p></div>`:''}
    ${focus(`<div class="cockpit-decision"><div class="row"><h3>Independent authorization</h3>${evaluation?demoStatus(evaluation.outcome):''}</div>${evaluation?`<div class="notice">${Object.entries(evaluation.layers).map(([k,v])=>esc(k.replaceAll('_',' '))+': '+v).join(' · ')}</div><div class="tour-checks">${evaluation.checks.map(c=>`<div class="${c.outcome!=='ALLOW'?'failed-check':''}">${icon(c.outcome==='ALLOW'?'check':'lock',16)}<span>${esc(c.name)}<small>${esc(c.layer.replaceAll('_',' '))} · ${esc(c.reason)}</small></span><b>${c.outcome==='ALLOW'?'PASS':c.outcome==='BLOCK'?'BLOCK':'REVIEW'}</b></div>`).join('')}</div>`:`<div class="awaiting-engine">${icon('shield',30)}<strong>Waiting for the proposal</strong><p>No trade executed.</p></div>`}</div>`,step===5,'Both mandate and ceiling must pass')}
    <div class="cockpit-feed"><h3>${icon('activity')} Investment activity & audit</h3>${state.events.slice(0,4).map(e=>`<div><span class="audit-time">${esc(e.time.slice(11))}</span><div><strong>${esc(e.kind)}</strong><small>${esc(e.text)}</small></div>${demoStatus(e.kind)}</div>`).join('')||'<p>Waiting for submission.</p>'}</div></section></div>
    <div class="tour-control-bar"><div class="next-explanation"><span class="waiting-dot"></span><div><strong>${typing?(step===3?'Typing the request…':'Demo cursor is completing setup…'):step===6?'Walkthrough complete':'Waiting for you to continue'}</strong><small>${esc(beat.description)}</small></div></div><div class="tour-buttons"><button class="secondary" data-reset-demo ${locked?'disabled':''} aria-label="Restart demo">${icon('restart')}</button><button class="secondary" data-demo-back ${locked||step<0?'disabled':''}>${icon('left')} Back</button>${step<6?`<button class="presenter-next" data-demo-next ${locked?'disabled':''}>${step<0?'Connect Kalshi Demo':beat.next} ${icon('right')}</button>`:'<button data-reset-demo>Replay walkthrough →</button>'}</div></div></div>`;
}

async function animateDemoSetup(kind){
  const run=++demoRun, account=kind==='account', values=account?demoAccount:demoAgent, source=account?demoAccountValues:demoAgentValues;
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  typing=true;
  for(const [field,value] of Object.entries(source)){
    if(run!==demoRun||!demo)return;
    demoActive=field;demoClick=false;render();
    document.querySelector(`[data-demo-field="${field}"]`)?.closest('.demo-field')?.scrollIntoView({block:'center',behavior:reduced?'auto':'smooth'});
    if(!reduced)await demoSleep(300);
    if(run!==demoRun||!demo)return;
    demoClick=true;render();
    const select=['provider','model','account'].includes(field);
    if(select){demoMenu=field;render();if(!reduced)await demoSleep(550);}
    if(run!==demoRun||!demo)return;
    if(select||reduced){values[field]=value;demoMenu='';render();}
    else for(let i=1;i<=value.length;i++){
      await demoSleep(45);
      if(run!==demoRun||!demo)return;
      values[field]=value.slice(0,i);
      const input=document.querySelector(`[data-demo-field="${field}"]`);
      if(input)input.value=values[field];
    }
    if(!reduced)await demoSleep(250);
  }
  if(run!==demoRun||!demo)return;
  demoActive=account?'connectAccount':'saveAgent';render();
  document.querySelector('.demo-submit')?.scrollIntoView({block:'center',behavior:reduced?'auto':'smooth'});
  if(!reduced)await demoSleep(700);
  if(run!==demoRun||!demo)return;
  if(account)demoAccountConnected=true;else demoAgentConnected=true;
  typing=false;demoActive='';demoClick=false;render();
}
async function typeDemo(){
  const run=++demoRun;typed='';typing=true;render();
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.querySelector('.typing-request')?.scrollIntoView({block:'center',behavior:reduced?'auto':'smooth'});
  for(let i=1;i<=demoScene.request.length;i++){
    if(!reduced)await demoSleep(65);
    if(run!==demoRun||!demo)return;
    typed=demoScene.request.slice(0,i);
    const el=document.querySelector('.typing-request');if(el)el.innerHTML=esc(typed)+'<span class="typing-caret">|</span>';
  }
  typing=false;render();
}
function restoreDemoPresentation(target){
  demoAccount=target>=0?{...demoAccountValues}:{};demoAgent=target>=1?{...demoAgentValues}:{};
  demoAccountConnected=target>=0;demoAgentConnected=target>=1;typed=target>=3?demoScene.request:'';
}
async function resetDemo(){
  if(!demo||busy)return;
  stopDemoAnimation();step=-1;demoTx=null;restoreDemoPresentation(-1);await api('reset');
  if(demo&&!error)document.querySelector('.demo-workspace')?.scrollIntoView({block:'start'});
}
async function runDemo(target){
  if(!demo||target!==5)return;
  const m=state.markets.find(m=>m.agent===demoScene.category);
  const o=await api('trade',{agent:demoScene.agentId,market:m.id,amount:demoScene.amount,direction:'Yes',request:demoScene.reason,key:'single-demo-trade'});
  demoTx=o?.id;
}
async function demoNext(){
  if(!demo||busy||typing||transition||step>=6)return;
  const run=demoRun;transition=true;await runDemo(step+1);
  if(run!==demoRun||!demo)return;
  transition=false;if(error){render();return;}step++;
  if(step===0)await animateDemoSetup('account');else if(step===1)await animateDemoSetup('agent');else if(step===3)await typeDemo();else render();
}
async function demoBack(){
  if(!demo||busy||typing||transition||step<0)return;
  stopDemoAnimation();const target=step-1,run=demoRun;transition=true;await api('reset');
  if(run!==demoRun||!demo)return;
  if(error){transition=false;render();return;}
  demoTx=null;restoreDemoPresentation(target);await runDemo(target);
  if(run!==demoRun||!demo)return;
  step=target;transition=false;render();
}
