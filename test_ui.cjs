const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function fixture(){
 const handlers=new Map(),docHandlers=new Map();let form;
 const element=selector=>({addEventListener:(event,fn)=>handlers.set(selector+':'+event,fn)});
 const context={console,document:{querySelectorAll:s=>[element(s)],querySelector:s=>s==='#account-form'?form:null,addEventListener:(e,f)=>docHandlers.set(e,f)},sessionStorage:{removeItem(){}},FormData:class {constructor(){return new Map([['name','Demo'],['connectionType','kalshi-demo'],['identifier','id'],['private_key','test-fixture'],['subaccount','0']])}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('static/app.js','utf8').replace(/;load\(\);/,';'),context);
 vm.runInContext("render=()=>{};state={accounts:[],agentConfigs:[]};api=async()=>{};bind()",context);
 return {context,handlers,docHandlers,run:s=>vm.runInContext(s,context),click:(s,event={})=>handlers.get(s+':click')({currentTarget:{dataset:{...event}},preventDefault(){}}),form:()=>{form={};vm.runInContext('extraBind()',context);return form}};
}
test('creation goes straight to Kalshi and all abandonment paths clear pending state',async()=>{
 const f=fixture();await f.click('[data-create-agent]');assert.equal(f.run('modal.type'),'account');assert.equal(f.run('modal.forAgent'),true);
 await f.click('[data-page]',{page:'Overview'});assert.equal(f.run('pendingCreateAgent'),false);
 await f.click('[data-create-agent]');f.docHandlers.get('keydown')({key:'Escape'});assert.equal(f.run('pendingCreateAgent'),false);
 await f.click('[data-create-agent]');await f.click('[data-close]');assert.equal(f.run('pendingCreateAgent'),false);
 await f.click('[data-create-agent]');await f.click('[data-signout]');assert.equal(f.run('pendingCreateAgent'),false);
});
test('failed account test stays on connections, unrelated retry cannot resume, matching retry can',async()=>{
 const f=fixture();await f.click('[data-create-agent]');
 f.run("api=async action=>{if(action==='account-save')state.accounts.push({id:'saved',venue:'kalshi',status:'disconnected'});else if(action==='account-test')state.accounts[0].status='error'}");
 await f.form().onsubmit({preventDefault(){}});assert.equal(f.run('modal'),null);assert.equal(f.run('page'),'Connected Accounts');assert.equal(f.run('pendingCreateAgent'),true);
 f.run("api=async(action,{id})=>{if(id==='saved')state.accounts[0].status='connected';state.accounts[0].stale=false}");
 await f.click('[data-test-account]',{testAccount:'other'});assert.equal(f.run('modal'),null);
 await f.click('[data-test-account]',{testAccount:'saved'});assert.equal(f.run('modal.type'),'agent');assert.equal(f.run('modal.draft.accountId'),'saved');assert.equal(f.run('pendingCreateAgent'),false);
});
test('navigation away after failed connection cancels later retry continuation',async()=>{
 const f=fixture();f.run("pendingCreateAgent=true;pendingAgentAccountId='saved';state.accounts=[{id:'saved',status:'error'}]");
 await f.click('[data-page]',{page:'Overview'});f.run("api=async()=>{state.accounts[0].status='connected';state.accounts[0].stale=false}");await f.click('[data-test-account]',{testAccount:'saved'});assert.equal(f.run('modal'),null);
});
test('effective UI exposes workspace cash and live restrictions despite loose overrides',()=>{
 const f=fixture();f.run("scope='a';state.permissions={dailyCap:100,exposureCap:500,dailyLossCap:50,cashFloor:200,liveTrading:false,automation:true};state.accounts=[{id:'a',name:'Test',environment:'sandbox',ceiling:{cashFloor:0,liveTrading:true,dailyCap:999},effectiveCeiling:{dailyCap:100,exposureCap:500,dailyLossCap:50,cashFloor:200,liveTrading:false}}]");const html=f.run('settings()');assert(html.includes('Effective account ceiling: $200.00'));assert(html.includes('Effective live trading: Disabled'));
});
