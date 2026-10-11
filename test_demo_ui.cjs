const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function walkthrough(reduced=true){
  const calls=[],timers=[];
  const context=vm.createContext({demo:true,step:-1,busy:false,error:'',
    window:{matchMedia:()=>({matches:reduced})},
    document:{querySelector:()=>null},render:()=>{},
    setTimeout:resolve=>timers.push(resolve),
    state:{markets:[{id:'economics-test',agent:'economics'}]},
    api:async(action,values)=>{calls.push({action,values});return action==='trade'?{id:'simulated-order'}:null;},
  });
  vm.runInContext(fs.readFileSync(__dirname+'/static/demo.js','utf8'),context);
  return {context,calls,timers,read:expression=>vm.runInContext(expression,context)};
}

test('onboarding stays presentation-only and the original trade runs once after setup',async()=>{
  const w=walkthrough();
  await w.read('resetDemo()');w.calls.length=0;
  await w.read('demoNext()');
  assert.equal(w.read('demoAccountConnected'),true);
  assert.equal(w.read('demoAccount.accountName'),'My Kalshi Demo Account');
  await w.read('demoNext()');
  assert.equal(w.read('demoAgentConnected'),true);
  assert.equal(w.read('demoAgent.provider'),'OpenAI');
  assert.equal(w.read('demoAgent.model'),'GPT-5');
  await w.read('demoNext()'); // Mandate
  await w.read('demoNext()'); // Request typing
  assert.equal(w.read('typed'),w.read('demoScene.request'));
  await w.read('demoNext()'); // Proposal
  assert.equal(w.calls.length,0,'Account and agent setup must not save credentials or call providers');
  await w.read('demoNext()'); // Independent governance, simulated trade
  await w.read('demoNext()'); // Result
  await w.read('demoNext()'); // Cannot advance past the result
  assert.equal(w.context.step,6);
  assert.equal(w.calls.length,1);
  assert.equal(w.calls[0].action,'trade');
  assert.equal(w.calls[0].values.agent,'demo-conservative');
  assert.equal(w.calls[0].values.amount,5);
});

test('exit cancels pending typing and fast repeated clicks cannot skip onboarding',async()=>{
  const w=walkthrough(false);
  const first=w.read('demoNext()');
  await w.read('demoNext()');
  await Promise.resolve();
  assert.equal(w.context.step,0);
  assert.equal(w.read('typing'),true);
  w.context.demo=false;w.read('stopDemoAnimation()');
  for(const resolve of w.timers.splice(0))resolve();
  await first;
  assert.equal(w.read('typing'),false);
  assert.equal(w.read('demoAccountConnected'),false);
  assert.equal(w.read('demoActive'),'');
  assert.equal(w.calls.length,0);
  await w.read('demoNext()');
  assert.equal(w.calls.length,0,'Leaving demo must prevent further simulated or real actions');
});

test('back and restart clear the simulated trade and restore setup correctly',async()=>{
  const w=walkthrough();
  for(let i=0;i<7;i++)await w.read('demoNext()');
  await w.read('demoBack()'); // Return to checks, rebuilding the same one-trade simulation
  assert.equal(w.context.step,5);
  assert.equal(w.read('demoTx'),'simulated-order');
  await w.read('demoBack()'); // Return to the proposal, before any trade
  assert.equal(w.context.step,4);
  assert.equal(w.read('demoTx'),null);
  await w.read('resetDemo()');
  assert.equal(w.context.step,-1);
  assert.equal(w.read('demoAccountConnected'),false);
  assert.equal(w.read('demoAgentConnected'),false);
  assert.equal(w.read('typed'),'');
  assert.ok(w.calls.every(call=>['reset','trade'].includes(call.action)));
});

test('a failed governance submission leaves the presenter on the proposal step',async()=>{
  const w=walkthrough();
  for(let i=0;i<5;i++)await w.read('demoNext()');
  w.context.api=async()=>{w.context.error='Simulated request failed';};
  await w.read('demoNext()');
  assert.equal(w.context.step,4);
  assert.equal(w.read('transition'),false);
  assert.equal(w.read('demoTx'),undefined);
});
