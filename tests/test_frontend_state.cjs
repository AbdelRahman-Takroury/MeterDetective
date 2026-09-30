/* Run with node --test tests/test_frontend_state.cjs; no extra dependencies. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
const stylesSource = fs.readFileSync(path.join(__dirname, '../frontend/styles.css'), 'utf8');

function harness() {
  const nodes = new Map();
  const saved = new Map();
  const media = {matches:false,listener:null,addEventListener(_event,callback){this.listener=callback;}};
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {innerHTML:'', textContent:'', className:'',
      disabled:false, dataset:{}, style:{}, attributes:{}, matches(){return false;},
      setAttribute(name,value){this.attributes[name]=String(value);},
      addEventListener(event,callback){if(event==='click')this.onclick=callback;},
      classList:{toggle(){}}, querySelectorAll(){return [];},
      querySelector(selector){return selector==='form'||selector.includes('input,textarea')?null:node(id + '-child');}, focus(){}});
    return nodes.get(id);
  };
  const context = vm.createContext({document:{getElementById:node,documentElement:{dataset:{}}},
    location:{hash:'#/live'}, sessionStorage:{getItem(){return null;},setItem(){},removeItem(){}},
    localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)},
    setTimeout(){return 1;}, clearTimeout(){}, console, window:{matchMedia:()=>media},
    fetch:async () => {throw Error('unexpected request');}});
  // Only bootstrap attaches browser listeners. Exercise the actual production functions.
  vm.runInContext(source.slice(0,source.indexOf('document.querySelector(".skip")')), context);
  return {context, node, saved, media, run:code=>vm.runInContext(code,context), set:(name,value)=>context[name]=value};
}
function detail({decision=null, actionStatus='pending_approval', status='awaiting_approval', result=null}={}) {
  return {case:{id:'case-1',status},meter_ids:['M1'],evidence:[],hypotheses:[],plans:[],
    recommendations:[{id:'r1',status:decision||'pending_approval',requires_approval:true,
      action_type:'technician_inspection',rationale:'Stored evidence',risk:'medium',
      approval:decision?{decision,decided_by:'Operator'}:null}],
    actions:[{id:'a1',recommendation_id:'r1',status:actionStatus,result:{},executed_at:'2026-09-20T13:00:00Z'}],
    case_events:result?[{event_type:'verification_completed',details:{action_id:'a1'}}]:[],
    latest_report:{answers:result?[{question_id:15,status:'answered',answer:'Stored outcome',
      structured_values:{deterministic_status:result}}]:[]}};
}

test('startup and restored-case loading never claim no investigation',()=>{
  const h=harness();
  assert.equal(h.run('investigationPresence()'),'No investigation open');
  h.run('live.phase="investigating_initial_anomaly"');
  assert.equal(h.run('investigationPresence()'),'Investigation starting…');
  assert.ok(h.run('liveSummary(null).flat().every(value=>!value.includes("No investigation"))'));
  h.run('live.phase="complete";live.caseId="persisted-id"');
  assert.equal(h.run('investigationPresence()'),'Loading investigation…');
});

test('system status renders safe component states and refresh control',()=>{
  const h=harness();h.set('data',{checked_at:'2026-09-29T10:00:00Z',
    application:{status:'ready',detail:'Responding.',critical:true},
    database:{status:'connected',detail:'History is stored.',critical:true},
    demonstration_dataset:{status:'missing',detail:'Prepare scenarios.',critical:true,count:3},
    knowledge_base:{status:'ready',detail:'Documents stored.',count:3},
    weather:{status:'cached',detail:'Using safe cached evidence.',observed_at:'2026-09-29T09:00:00Z'},
    narrative_service:{status:'disabled',detail:'Optional service is off.'},
    latest_investigation:{status:'failed',detail:'Review stored activity.'}});
  h.run('renderStatus(data)');const html=h.node('main').innerHTML;
  assert.match(html,/System status/);assert.match(html,/Simulation environment/);
  assert.match(html,/Using safe cached evidence/);assert.match(html,/Optional service is off/);
  assert.match(html,/3 items recorded/);assert.match(html,/Required for the demonstration workflow/);
  assert.equal(h.run('typeof document.getElementById("refresh-status").onclick'),'function');
});

test('theme follows the system until the operator chooses and persists an override',()=>{
  const h=harness();h.media.matches=true;h.run('initializeTheme()');
  assert.equal(h.context.document.documentElement.dataset.theme,'dark');
  assert.equal(h.node('theme-toggle').attributes['aria-pressed'],'true');
  assert.equal(h.node('theme-toggle').attributes['aria-label'],'Use light mode');
  h.node('theme-toggle').onclick();
  assert.equal(h.context.document.documentElement.dataset.theme,'light');
  assert.equal(h.saved.get('meterdetective-theme'),'light');
  h.media.listener({matches:true});
  assert.equal(h.context.document.documentElement.dataset.theme,'light');
});

test('theme responds to system changes when no explicit preference is stored',()=>{
  const h=harness();h.run('initializeTheme()');
  assert.equal(h.context.document.documentElement.dataset.theme,'light');
  h.media.listener({matches:true});
  assert.equal(h.context.document.documentElement.dataset.theme,'dark');
  assert.equal(h.saved.has('meterdetective-theme'),false);
});

test('theme still applies when browser storage is unavailable',()=>{
  const h=harness();h.context.localStorage.getItem=()=>{throw Error('blocked')};
  h.context.localStorage.setItem=()=>{throw Error('blocked')};
  assert.equal(h.run('applyTheme("dark",true)'),'dark');
  assert.equal(h.context.document.documentElement.dataset.theme,'dark');
});

test('theme is selected before styles load and both palettes use semantic tokens',()=>{
  const bootstrap=indexSource.indexOf('meterdetective-theme');
  const stylesheet=indexSource.indexOf('rel="stylesheet"');
  assert.ok(bootstrap>0&&bootstrap<stylesheet);
  assert.match(indexSource,/id="theme-toggle"[^>]+aria-pressed="false"/);
  assert.match(stylesSource,/\[data-theme="dark"\]\{color-scheme:dark/);
  for(const token of ['--surface','--warning-soft','--danger-soft','--chart-line']){
    assert.ok(stylesSource.includes(token),`missing semantic token ${token}`);
  }
});

test('demonstration guide is deterministic, complete, and linked to real panels',()=>{
  const h=harness();const html=h.run('demoGuide()');
  for(const text of ['How this demonstration works','Reading arrives','Anomaly detected',
    'Evidence gathered','Explanations compared','Next step proposed','Operator decides',
    'Outcome checked','Why is it agentic?','What data is synthetic?',
    'How is uncertainty handled?','What happens when a service fails?','Simulation only:']){
    assert.ok(html.includes(text),`missing guide copy: ${text}`);
  }
  for(const target of ['live-activity','live-evidence','live-hypotheses','live-recommendation']){
    assert.ok(html.includes(`data-guide-target="${target}"`));
  }
  assert.ok(!html.toLowerCase().includes('chatbot'));
});

test('guide shortcuts focus their corresponding investigation section',()=>{
  const h=harness();const target=h.node('live-evidence');let focused=false,scrolled=false;
  target.focus=()=>{focused=true;};target.scrollIntoView=()=>{scrolled=true;};
  assert.equal(h.run('focusGuideTarget("live-evidence")'),true);
  assert.equal(target.attributes.tabindex,'-1');assert.equal(focused,true);assert.equal(scrolled,true);
  h.context.document.getElementById=id=>id==='missing'?null:h.node(id);
  assert.equal(h.run('focusGuideTarget("missing")'),false);
});

test('status route shows loading, renders success, and has a retryable failure',async()=>{
  const h=harness();h.context.location.hash='#/status';
  const data={checked_at:'2026-09-29T10:00:00Z',application:{status:'ready',detail:'Ready'},
    database:{status:'connected',detail:'Connected'},demonstration_dataset:{status:'ready',detail:'Ready'},
    knowledge_base:{status:'ready',detail:'Ready'},weather:{status:'not_checked',detail:'Not checked'},
    narrative_service:{status:'disabled',detail:'Disabled'},latest_investigation:{status:'not_checked',detail:'None'}};
  h.set('fetch',async()=>({ok:true,json:async()=>data}));await h.run('route()');
  assert.match(h.node('main').innerHTML,/System status/);
  h.set('fetch',async()=>({ok:false,status:503,json:async()=>({detail:'Status service unavailable'})}));
  await h.run('route()');assert.match(h.node('main').innerHTML,/Status service unavailable/);
  assert.equal(h.run('typeof document.getElementById("retry-status").onclick'),'function');
});

for (const [config,title,pending] of [
  [{},'Human approval required',true],
  [{decision:'approved',actionStatus:'ready',status:'action_ready'},'Approved — simulated action ready',false],
  [{decision:'approved',actionStatus:'completed',status:'pending_verification'},'Pending verification',false],
  [{decision:'rejected',actionStatus:'cancelled'},'Recommendation rejected',false],
  [{decision:'approved',actionStatus:'completed',status:'resolved',result:'verified'},'Investigation resolved',false],
  [{decision:'approved',actionStatus:'completed',status:'reopened',result:'not_verified'},'not verified',false],
  [{decision:'approved',actionStatus:'completed',status:'monitoring',result:'insufficient_evidence'},'insufficient evidence',false],
]) test(`workflow presentation: ${title}`,()=>{
  const h=harness();h.set('d',detail(config));
  assert.equal(h.run('currentWorkflow(d).title'),title);
  assert.equal(h.run('currentWorkflow(d).approvalPending'),pending);
  const html=h.run('liveRecommendation(d)');
  assert.equal(html.includes('Human approval required'),pending);
  h.run('workflowControls(d,currentRecommendation(d))');
  const controls=h.node('workflow-controls').innerHTML;
  assert.equal(controls.includes('Approve simulated action'),pending);
  if(config.actionStatus==='completed')assert.ok(!controls.includes('Record simulated inspection'));
});

test('superseded recommendations and other-action verification are not current',()=>{
  const h=harness(),d=detail({decision:'approved',actionStatus:'completed'});
  d.recommendations.push({id:'old',status:'superseded'});
  d.case_events=[{event_type:'verification_completed',details:{action_id:'different'}}];
  d.latest_report.answers=[{question_id:15,structured_values:{deterministic_status:'verified'}}];
  h.set('d',d);
  assert.equal(h.run('currentRecommendation(d).id'),'r1');
  assert.equal(h.run('currentWorkflow(d).result'),null);
  assert.equal(h.run('currentWorkflow(d).title'),'Pending verification');
});

test('replay completion is not workflow completion',()=>{
  const h=harness();h.set('d',detail());h.run('live.detail=d;live.phase="complete"');
  assert.equal(h.run('livePhase()[0]'),'Evidence stream complete');
  assert.match(h.run('livePhase()[1]'),/Human approval required/);
  h.set('d',detail({decision:'approved',actionStatus:'completed',status:'pending_verification'}));
  h.run('live.detail=d');assert.match(h.run('livePhase()[1]'),/Pending verification/);
  h.run('live.detail.case.status="resolved"');assert.equal(h.run('livePhase()[0]'),'Investigation resolved');
});

test('follow-up defaults use stored windows only and remain editable',()=>{
  const h=harness(),d=detail({decision:'approved',actionStatus:'completed'});
  d.actions[0].result.verification_window={start:'2026-09-20T12:30:00Z',end:'2026-09-20T14:30:00Z'};
  h.set('d',d);h.run('workflowControls(d,currentRecommendation(d))');
  const html=h.node('workflow-controls').innerHTML;
  assert.match(html,/value="2026-09-20T12:30"/);assert.match(html,/value="2026-09-20T14:30"/);
  assert.ok(!html.includes('readonly'));assert.match(html,/inputs only/);
  delete d.actions[0].result.verification_window;
  h.run('workflowControls(d,currentRecommendation(d))');
  assert.match(h.node('workflow-controls').innerHTML,/No stored follow-up window/);
  assert.ok(!h.node('workflow-controls').innerHTML.includes('value="2026'));
  d.scenario={repair_window_start:'2026-09-20T12:30:00Z',repair_window_end:'2026-09-20T14:30:00Z'};
  assert.equal(h.run('verificationWindow(d,d.actions[0]).start'),d.scenario.repair_window_start);
});

test('100% stays numerically unchanged, with competing and contradictory evidence visible',()=>{
  const h=harness(),d=detail();d.hypotheses=[{label:'upstream',confidence:1,
    supporting_evidence:['support-id'],contradicting_evidence:['contradiction-id']},
    {label:'data_quality',confidence:0.3,supporting_evidence:[],contradicting_evidence:[]}];
  h.set('d',d);const html=h.run('liveHypotheses(d)');
  assert.match(html,/Current evidence support:<\/small> 100%/);assert.match(html,/not proof of root cause/);
  assert.match(html,/contradiction-id/);assert.match(html,/Reading or communication problem/);
  assert.equal(d.hypotheses[0].confidence,1);
});

test('report approval snapshot does not override the recorded decision',()=>{
  const h=harness(),d=detail({decision:'approved',actionStatus:'completed'});
  d.latest_report.answers=[{question_id:14,status:'answered',answer:'Human approval required',
    structured_values:{approval_status:'pending'},confidence:1}];h.set('d',d);
  const html=h.run('questionPanel(answerMap(d),d)');
  assert.ok(!html.includes('Human approval required'));
  assert.match(html,/Pending verification/);assert.match(html,/Stored report snapshot/);
});

test('unknown numbers stay unknown, real zeros stay zero, timestamps explicitly UTC',()=>{
  const h=harness();
  assert.equal(h.run('money(null)'),'Unknown');assert.equal(h.run('money(NaN)'),'Unknown');
  assert.equal(h.run('money(0)'),'0 JOD');assert.equal(h.run('percent(null)'),'Unknown');
  assert.equal(h.run('money("0.702")'),'0.702 JOD');assert.equal(h.run('money("0")'),'0 JOD');
  assert.equal(h.run('fixed("0.1200",3)'),'0.120');assert.equal(h.run('money("")'),'Unknown');
  assert.equal(h.run('money(false)'),'Unknown');assert.equal(h.run('money("Infinity")'),'Unknown');
  assert.equal(h.run('fixed(null,2)'),'Unknown');
  assert.match(h.run('date("2026-09-20T15:00:00+03:00")'),/12:00:00 UTC$/);
  assert.equal(h.run('localInput("invalid")'),'');
});

test('stream results reflect API evidence, never the stage name alone',()=>{
  const h=harness();h.run('addStream("shared","M2",{status:"normal",anomaly_count:0,case_id:null})');
  assert.equal(h.run('live.stream[0].label'),'Reading within expected range');
  assert.ok(!h.run('live.stream[0].value').includes('case updated'));
});

test('a transient case refresh retains case and session identity',async()=>{
  const h=harness();h.set('d',detail());h.run('live.caseId="case-1";live.detail=d;renderLive=()=>{}');
  h.set('fetch',async()=>{throw Error('offline');});
  await assert.rejects(h.run('refreshLiveCase()'),/offline/);
  assert.equal(h.run('live.caseId'),'case-1');assert.equal(h.run('live.detail.case.id'),'case-1');
});

test('unchanged poll avoids rerender, changed poll updates authoritative case',async()=>{
  const h=harness();h.set('d',detail());
  h.run('live.caseId="case-1";live.detail=d;let renders=0;renderLive=()=>{renders++}');
  h.set('fetch',async url=>({ok:true,json:async()=>url.endsWith('/trace')?{runs:[]}:dClone}));
  let dClone=JSON.parse(JSON.stringify(h.context.d));
  await h.run('refreshLiveCase()');assert.equal(h.run('renders'),0);
  dClone=detail({decision:'approved',actionStatus:'completed',status:'pending_verification'});
  await h.run('refreshLiveCase()');assert.equal(h.run('renders'),1);
  assert.equal(h.run('currentWorkflow(live.detail).title'),'Pending verification');
});

test('out-of-order refresh and navigation cannot overwrite the current case',async()=>{
  const h=harness();let resolve;h.run('live.caseId="case-1";renderLive=()=>{throw Error("stale render")}');
  h.set('fetch',()=>new Promise(r=>{resolve=r;}));const waiting=h.run('refreshLiveCase()');
  h.run('generation++;live.caseId="case-2"');
  resolve({ok:true,json:async()=>detail()});await waiting;
  assert.equal(h.run('live.detail'),null);assert.equal(h.run('live.caseId'),'case-2');
});

test('trace failure does not discard a successful case update',async()=>{
  const h=harness();h.run('live.caseId="case-1";renderLive=()=>{}');
  h.set('fetch',async url=>{if(url.endsWith('/trace'))throw Error('trace offline');return {ok:true,json:async()=>detail()};});
  await h.run('refreshLiveCase()');assert.equal(h.run('live.detail.case.id'),'case-1');
});

test('duplicate mutation clicks issue only one POST',async()=>{
  const h=harness();let resolve,calls=0;h.run('renderLive=()=>{}');
  h.set('fetch',()=>{calls++;return new Promise(r=>{resolve=r;});});
  const pending=h.run('mutate("/recommendations/r1/approve",{},"Done")');
  await h.run('mutate("/recommendations/r1/approve",{},"Done")');assert.equal(calls,1);
  resolve({ok:true,json:async()=>({})});await pending;assert.equal(h.run('busy'),false);
});

test('confirmed mutation followed by failed refresh removes stale action controls',async()=>{
  const h=harness();h.run('live.caseId="case-1";renderLive=()=>{}');
  h.set('fetch',async(url,opts)=>{if(opts.method==='POST')return {ok:true,json:async()=>({})};throw Error('offline');});
  await h.run('mutate("/actions/a1/execute-simulation",{},"Recorded")');
  assert.match(h.node('live-workflow-controls').innerHTML,/Change recorded/);
  assert.ok(!h.node('live-workflow-controls').innerHTML.includes('Record simulated inspection'));
  assert.equal(h.run('refreshRequired'),true);
});

test('polling preserves editable form values',()=>{
  const h=harness();const saved={operator:{value:'Alice'},start:{value:'2026-09-20T12:30'}};
  h.node('main').querySelectorAll=()=>[{id:'decision-r1',querySelectorAll:()=>[
    {name:'operator',value:'Alice'},{name:'start',value:'2026-09-20T12:30'}]}];
  h.node('decision-r1').elements={namedItem:name=>saved[name]};
  h.run('const snapshot=captureForms()');saved.operator.value='';saved.start.value='';
  h.run('restoreForms(snapshot)');assert.equal(saved.operator.value,'Alice');assert.equal(saved.start.value,'2026-09-20T12:30');
});

test('restored identity does not invent replay completion',async()=>{
  const h=harness();h.context.sessionStorage.getItem=key=>key==='meterdetective-live-case'?'case-1':null;
  h.run('renderLive=()=>{}');h.set('fetch',async url=>({ok:true,json:async()=>url.endsWith('/trace')?{runs:[]}:detail()}));
  await h.run('route()');
  assert.equal(h.run('live.phase'),'restored');assert.equal(h.run('livePhase()[0]'),'Stored investigation');
  assert.match(h.run('liveStream()'),/earlier replay log is unavailable/);
});

test('persisted replay progress is restored without guessing case outcome',async()=>{
  const h=harness();h.context.sessionStorage.getItem=key=>key==='meterdetective-live-case'?'case-1':JSON.stringify({caseId:'case-1',phase:'complete',progress:9,stream:[{meter:'M1',time:'2026-09-20 12:00',label:'Reading processed'}]});
  h.run('renderLive=()=>{}');h.set('fetch',async url=>({ok:true,json:async()=>url.endsWith('/trace')?{runs:[]}:detail()}));
  await h.run('route()');assert.equal(h.run('live.progress'),9);
  assert.equal(h.run('livePhase()[0]'),'Evidence stream complete');assert.equal(h.run('live.stream.length'),1);
  assert.equal(h.run('currentWorkflow(live.detail).approvalPending'),true);
});

test('successful response updates approval/action presentation even before case refresh',()=>{
  const h=harness(),d=detail();h.set('d',d);
  h.set('response',{recommendation:{...d.recommendations[0],status:'executed'},
    approval:{decision:'approved',decided_by:'Operator'},
    action:{...d.actions[0],status:'completed',result_json:{simulation:true}}});
  h.run('applyWorkflowResponse(d,response)');
  assert.equal(h.run('currentWorkflow(d).approvalPending'),false);
  assert.equal(h.run('currentWorkflow(d).title'),'Pending verification');
  assert.equal(h.run('d.actions[0].result.simulation'),true);
  assert.ok(!h.run('liveRecommendation(d)').includes('Human approval required'));
});

test('restored-case polling failure locks stale workflow until a fresh read',async()=>{
  const h=harness();h.run('live.caseId="case-1";renderLive=()=>{}');
  h.set('fetch',async()=>{throw Error('offline');});await h.run('route(true)');
  assert.equal(h.run('refreshRequired'),true);
  assert.equal(h.run('live.refreshError'),'offline');
  assert.equal(h.run('live.caseId'),'case-1');
});

test('an unchanged poll cannot strand an in-flight chart in loading state',async()=>{
  const h=harness(),d=detail();d.latest_report.answers=[{question_id:1,fresh_as_of:'2026-09-20T12:00:00Z'}];h.set('d',d);
  let resolve;h.set('fetch',()=>new Promise(r=>{resolve=r;}));
  const chart=h.run('loadChart(d,generation)');h.run('generation++');
  resolve({ok:true,json:async()=>({items:[{timestamp:'2026-09-20T12:00:00Z',kwh:3}]})});
  await chart;assert.match(h.node('chart').innerHTML,/1 observations/);
});

test('live screen renders recorded action without approval or completion claims',()=>{
  const h=harness();h.set('d',detail({decision:'approved',actionStatus:'completed',status:'pending_verification'}));
  h.run('live.detail=d;live.caseId=d.case.id;live.phase="complete";renderLive()');
  const html=h.node('main').innerHTML;
  assert.ok(!html.includes('Human approval required'));assert.ok(!html.includes('Demonstration complete'));
  assert.match(html,/Evidence stream complete/);assert.match(html,/Pending verification/);
  assert.match(h.node('live-workflow-controls').innerHTML,/Approved simulated action recorded/);
});

// Track DOM destruction explicitly: restoring text after innerHTML is not a pass.
function editingHarness(config={},fieldName='comment'){
  const h=harness();h.set('d',detail(config));
  h.run('live.caseId=d.case.id;live.detail=d;live.phase="complete";renderLive()');
  const field={name:fieldName,value:fieldName==='comment'?'Investigate connection before replacement':'2026-09-20T13:15',
    disabled:false,selectionStart:12,selectionEnd:22,selectionDirection:'backward',isConnected:true};
  h.context.document.activeElement=field;
  let mainWrites=0,formWrites=0;
  for(const [id,onWrite] of [['main',()=>mainWrites++],['live-workflow-controls',()=>formWrites++]]){
    const target=h.node(id);let html=target.innerHTML;
    Object.defineProperty(target,'innerHTML',{get:()=>html,set:value=>{
      onWrite();html=value;field.isConnected=false;h.context.document.activeElement=null;
    }});
  }
  const regions=new Map();
  h.node('main').querySelector=selector=>{
    if(selector==='#play-demo')return h.node('play-demo');
    if(!regions.has(selector))regions.set(selector,h.node('region:'+selector));
    return regions.get(selector);
  };
  h.node('main').querySelectorAll=selector=>selector==='button'?[h.node('submit')]:[];
  let next=JSON.parse(JSON.stringify(h.context.d));
  h.set('fetch',async url=>({ok:true,json:async()=>url.endsWith('/trace')?{runs:[]}:next}));
  return {...h,field,regions,writes:()=>[mainWrites,formWrites],setNext:value=>{next=value;}};
}

for(const [name,config,fieldName] of [
  ['approval/rejection comment',{},'comment'],
  ['operator name',{},'operator'],
  ['verification start',{decision:'approved',actionStatus:'completed',status:'pending_verification'},'start'],
  ['verification end',{decision:'approved',actionStatus:'completed',status:'pending_verification'},'end'],
])test(`${name}: changed evidence polling keeps the exact field, draft, focus and selection`,async()=>{
  const h=editingHarness(config,fieldName),original=h.field.value;
  const next=detail(config);next.case.updated_at='2026-09-29T16:00:00Z';
  next.evidence=[{kind:'peer_comparison',value:{status:'answered',peer_count:3}}];
  h.setNext(next);
  await h.run('route(true)');await h.run('route(true)');
  assert.deepEqual(h.writes(),[0,0],'polling must not recreate the page or form');
  assert.equal(h.field.isConnected,true);assert.equal(h.context.document.activeElement,h.field);
  assert.equal(h.field.value,original);assert.equal(h.field.selectionStart,12);
  assert.equal(h.field.selectionEnd,22);assert.equal(h.field.selectionDirection,'backward');
  assert.equal(h.field.disabled,false);
  assert.match(h.regions.get('#live-evidence .panel-body').innerHTML,/3 comparable meter readings/);
});

test('poll failure and recovery leave a focused comment editable and connected',async()=>{
  const h=editingHarness();h.set('fetch',async()=>{throw Error('temporary outage');});
  await h.run('route(true)');
  assert.deepEqual(h.writes(),[0,0]);assert.equal(h.field.disabled,false);
  assert.equal(h.context.document.activeElement,h.field);assert.equal(h.node('submit').disabled,true);
  h.set('fetch',async url=>({ok:true,json:async()=>url.endsWith('/trace')?{runs:[]}:detail()}));
  await h.run('route(true)');assert.deepEqual(h.writes(),[0,0]);
  assert.equal(h.field.disabled,false);assert.equal(h.node('submit').disabled,false);
});

test('unrelated busy rendering cannot recreate or disable editable operator fields',()=>{
  const h=editingHarness();h.run('busy=true;renderLive()');
  assert.deepEqual(h.writes(),[0,0]);assert.equal(h.field.disabled,false);
  assert.equal(h.context.document.activeElement,h.field);
});

for(const decision of ['approved','rejected'])test(`comment disappears after legitimate ${decision} transition`,async()=>{
  const h=editingHarness();
  h.setNext(detail({decision,actionStatus:decision==='approved'?'ready':'cancelled'}));
  await h.run('route(true)');
  assert.deepEqual(h.writes(),[1,1]);assert.equal(h.field.isConnected,false);
  assert.ok(!h.node('live-workflow-controls').innerHTML.includes('<textarea'));
});

test('verification draft survives evidence changes but form disappears when resolved',async()=>{
  const h=editingHarness({decision:'approved',actionStatus:'completed',status:'pending_verification'},'start');
  h.setNext(detail({decision:'approved',actionStatus:'completed',status:'monitoring',result:'insufficient_evidence'}));
  await h.run('route(true)');assert.deepEqual(h.writes(),[0,0]);assert.equal(h.field.isConnected,true);
  assert.match(h.regions.get('#live-r1-verification-result').innerHTML,/insufficient evidence/);
  h.setNext(detail({decision:'approved',actionStatus:'completed',status:'resolved',result:'verified'}));
  await h.run('route(true)');assert.deepEqual(h.writes(),[1,1]);
  assert.ok(!h.node('live-workflow-controls').innerHTML.includes('<form'));
});

test('passive updates never overwrite a panel containing another editable field',()=>{
  const h=harness(),panel=h.node('custom-panel');panel.innerHTML='Original editor';
  const field={value:'unsaved notes'};panel.querySelector=()=>field;
  h.node('main').querySelector=()=>panel;
  h.run('updatePassive("#custom-panel","replacement")');
  assert.equal(panel.innerHTML,'Original editor');assert.equal(field.value,'unsaved notes');
});

test('an unconfirmed submit does not discard the comment draft',async()=>{
  const h=editingHarness(),root=h.node('live-workflow-controls');let warning='';
  root.querySelector=selector=>selector==='form'?{id:'live-r1-decision'}:null;
  root.insertAdjacentHTML=(position,html)=>{assert.equal(position,'afterbegin');warning=html;};
  h.set('fetch',async()=>{throw Error('connection lost');});
  await h.run('mutate("/recommendations/r1/approve",{comment:"draft"},"Saved")');
  assert.deepEqual(h.writes(),[0,0]);assert.equal(h.context.document.activeElement,h.field);
  assert.equal(h.field.disabled,false);assert.match(warning,/Reload the case/);
});
