'use strict';
// Render/planner interaction checks without a browser or external dependencies.
// This harness checks generated markup and editor logic, not visual layout.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(){this.value='';this.innerHTML='';this.textContent='';this.dataset={};this.handlers={};this.elements=[];this.classList={add(){},remove(){}};}
 addEventListener(type,fn){this.handlers[type]=fn;} showModal(){this.open=true;} close(){this.open=false;} focus(){} reset(){for(const e of this.elements)e.value='';}
}
const elements=new Map(),get=s=>{if(!elements.has(s))elements.set(s,new Element());return elements.get(s);};
const form=get('#item-form');
for(const name of ['id','title','kind','group','status','previous_status','priority_day','submitted_at','block_date','block_time','due','due_time','minutes','start_by','next_action','action_minutes','url','notes','resources']){
 const element=new Element();element.name=name;form.elements.push(element);form.elements[name]=element;
}
elements.set('#kind-select',form.elements.kind);
const document={activeElement:{},querySelector:get,querySelectorAll:()=>[],handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}};
const task=(id,title,extra={})=>({id,title,kind:'Project',group:'EECS',status:'active',due:'2026-10-08',due_time:'17:00',minutes:180,next_action:'',guidance:{attention:false,reason:'Start-by date reached'},...extra});
const fixture={today:'2026-10-07',now:'2026-10-07T10:00:00-04:00',items:[task('project','Report',{priority_day:'2026-10-07',current_action:{id:'step1',text:'Read spec',minutes:20},plan_steps:[{id:'step1',text:'Read spec',minutes:20},{id:'step2',text:'Write',minutes:60}]}),task('oa','Acme OA',{kind:'OA',priority_day:'2026-10-07',minutes:60,guidance:{attention:true,reason:'OA expires in 31 hours'}}),task('finished','Completed homework',{kind:'Homework',status:'done'}),task('suggested','Suggestion',{guidance:{attention:false,reason:'Attempt ASAP'}})],suggestions:['suggested'],habits:{LeetCode:{done:false,streak:2,points:14},Applications:{done:false,streak:0,points:10},'System design':{done:false,streak:0,points:15}},course_links:Array.from({length:6},(_,n)=>({id:'link'+n,label:'EECS '+n,url:'https://canvas.example.edu/'+n})),practice:[],applications:[],rewards:[],ledger:[],jobs:[],settings:{},points:0,streak:2};
const writes=[];let allowDelete=false;let confirmations=0;
const context=vm.createContext({document,console,confirm:()=>{confirmations++;return allowDelete;},structuredClone,Intl,Date,Set,JSON,Number,String,Math,Blob,URL,crypto:require('node:crypto').webcrypto,setTimeout:()=>0,clearTimeout(){},setInterval(){},fetch:async(path,options)=>{if(options)writes.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>path==='/api/session'?{token:'test'}:options?{ok:true}:structuredClone(fixture)};},FormData:class{constructor(f){this.entries=f.elements.filter(e=>!e.disabled).map(e=>[e.name,e.value]);}[Symbol.iterator](){return this.entries[Symbol.iterator]();}}});
vm.runInContext(fs.readFileSync('app.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 const today=run('today()');
 assert.equal((today.match(/data-priority="oa"/g)||[]).length,1);
 assert.ok(!today.includes('Deadline watch'));
 assert.ok(today.includes('80 min estimated'));
 assert.ok(today.includes('Apply to 1 job'));
 assert.ok(today.includes('data-disclosure="suggestions"'));
 assert.ok(!today.includes('class="task"'));
 assert.match(today,/https:\/\/leetcode.com\/[^>]*target="_blank" rel="noopener noreferrer"/);
 run("state.points=525;state.rewards=[{title:'Earlier reward',threshold:100,unlocked:1,claimed:0},{title:'New headphones',threshold:1000,unlocked:0,claimed:0}]");
 const preview=run('rewardPreview()');assert.ok(preview.includes('New headphones'));assert.ok(preview.includes('525/1000 aura'));assert.ok(preview.includes('value="525"'));assert.ok(run('today()').indexOf('rewards-preview')>run('today()').indexOf('<aside'));
 run('state.points=-30');assert.ok(run('rewardPreview()').includes('value="0"'));assert.ok(run('rewardPreview()').includes('-30/1000 aura'));
 run('state.points=0;state.rewards=[]');assert.ok(run('rewardPreview()').includes('No rewards yet'));
 const links=run('shortcuts()');assert.match(links,/More courses \(2\)/);assert.match(links,/target="_blank" rel="noopener noreferrer"/);
 run("filter='All'");const coursework=run('deadlines(true)');assert.ok(!coursework.includes('Completed homework'));
 run("filter='done'");assert.ok(run('deadlines(true)').includes('Completed homework'));
 assert.ok(!Array.from(run('tabs')).includes('Practice'));
 assert.ok(today.includes('class="upcoming-panel panel"'));
 assert.ok(!today.includes('data-disclosure="upcoming"'));
 assert.ok(today.includes('<strong>Acme OA</strong>')); // compact deadline reference remains visible even when selected
 assert.ok(!run('week()').includes('The next seven days.'));
 run("state.items.forEach(i=>i.priority_day='')");assert.ok(!run('today()').includes('Choose one specific action.'));
 run("state.habits.LeetCode.done=true;state.habits.Applications.done=true;state.habits['System design'].done=true;state.practice=[{id:'sd',kind:'System design',day:state.today,minutes:10,created:'2026-10-07T15:00:00-04:00'}]");
 assert.equal((run('today()').match(/habit-complete/g)||[]).length,3);
 run("state.habits.LeetCode.done=false");assert.equal((run('today()').match(/habit-complete/g)||[]).length,2);
 run("state.practice=Array.from({length:6},(_,n)=>({id:'log'+n,kind:'LeetCode',day:state.today,minutes:10,created:'2026-10-07T1'+n+':00:00-04:00'}));state.applications=[{id:'newest-job',day:state.today,company:'Acme',title:'Engineer',created:'2026-10-07T18:00:00-04:00'}]");
 let history=run('eventHistory()');assert.equal((history.match(/data-log=/g)||[]).length,5);assert.ok(history.indexOf('newest-job')<history.indexOf('log5'));assert.ok(!history.includes('data-log="log0"'));assert.ok(history.includes('Show more'));assert.ok(history.includes('application-edit'));
 assert.ok(history.includes('>Edit</button>'));assert.ok(history.includes('>Delete</button>'));assert.ok(!history.includes('Undo log'));
 await document.handlers.click({target:{closest:s=>s==='[data-action]'?{dataset:{action:'history-view'}}:null}});
 assert.equal(run('view'),'History');assert.equal((run('history()').match(/data-log=/g)||[]).length,8); // seven habit logs and legacy completed homework
 assert.match(run('history()'),/class="history-day" data-disclosure="history-2026-10-07"/);assert.ok(!run('history()').includes(' open'));
 const before=writes.length;
 await document.handlers.click({target:{closest:s=>s==='[data-action]'?{dataset:{action:'practice-delete',id:'log0'}}:null}});
 assert.equal(writes.length,before);assert.equal(confirmations,1);
 allowDelete=true;
 await document.handlers.click({target:{closest:s=>s==='[data-action]'?{dataset:{action:'practice-delete',id:'log0'}}:null}});
 assert.equal(writes.at(-1).path,'/api/practice-delete');assert.equal(confirmations,2);
 run("state.items.push({id:'slotted-no-date',title:'Work block',kind:'Project',status:'active',group:'EECS',due:'',block_date:state.today,block_time:'13:00',guidance:{attention:false}})");
 assert.ok(run('today()').includes('data-slotted="slotted-no-date"'));
 run("state.habits.LeetCode.done=true;state.habits.Applications.done=true;state.habits['System design'].done=true;state.habits.LeetCode.streak=3;state.habits.Applications.streak=5;state.habits['System design'].streak=5");
 const done=run('today()');assert.ok(!done.includes('data-action="lc"'));assert.ok(!done.includes('data-action="application"'));assert.ok(!done.includes('data-action="design"'));assert.ok(done.includes('One problem submission'));assert.ok(done.includes('fire-small'));assert.equal((done.match(/fire-large/g)||[]).length,2);
 assert.equal(run('streakFire(2)'), '');assert.ok(run('streakFire(3)').includes('fire-small'));assert.ok(run('streakFire(5)').includes('fire-large'));
 run('state=structuredClone('+JSON.stringify(fixture)+')');
 run("edit({kind:'Homework',title:'Batch'})");
 assert.equal(get('#planner').hidden,false);
 get('#step-entry').value='Read spec\nOutline\nWrite draft';get('#add-step').onclick();
 assert.deepEqual(Array.from(run('planDraft.map(s=>s.text)')),['Read spec','Outline','Write draft']);
 run("edit({kind:'Homework',title:'Chat draft',plan_steps:[{id:'draft1',text:'Read'},{id:'draft2',text:'Solve'}]})");
 assert.equal(get('#planner').open,true);
 // Reverse entry always stores execution order; direction switches retain pending text.
 run("edit({kind:'Project',title:'Example'})");get('#plan-mode').value='backward';get('#plan-mode').onchange();
 for(const text of ['Submit report','Proofread','Write report','Inspect spec']){get('#step-entry').value=text;get('#add-step').onclick();}
 assert.deepEqual(Array.from(run('planDraft.map(s=>s.text)')),['Inspect spec','Write report','Proofread','Submit report']);
 get('#step-entry').value='Unsubmitted text';get('#plan-mode').value='forward';get('#plan-mode').onchange();assert.equal(get('#step-entry').value,'Unsubmitted text');
 assert.equal(run('planDraft.length'),4);
 run("planDraft[0].completed_at='2026-10-07T10:00:00-04:00'");const id=run('planDraft[0].id');
 get('#plan-list').handlers.click({target:{closest:()=>({dataset:{plan:'down',step:id}})}});
 assert.equal(run('planDraft[1].id'),id);assert.ok(run('planDraft[1].completed_at'));
 get('#plan-list').handlers.input({target:{dataset:{stepText:id},value:'Inspect updated spec'}});
 assert.equal(run('planDraft[1].text'),'Inspect updated spec');
 get('#plan-list').handlers.click({target:{closest:()=>({dataset:{plan:'remove',step:id}})}});
 get('#undo-step').onclick();assert.equal(run('planDraft[1].id'),id);assert.ok(run('planDraft[1].completed_at'));
 run("edit({kind:'Project',title:'Existing',next_action:'Read spec',current_action:{id:'legacy-action',text:'Read spec'},plan_entry_mode:'backward'})");
 get('#step-entry').value='Submit';get('#add-step').onclick();get('#step-entry').value='Write';get('#add-step').onclick();
 assert.deepEqual(Array.from(run('planDraft.map(s=>s.text)')),['Read spec','Write','Submit']);
 assert.equal(run('planDraft[0].id'),'legacy-action');
 // Daily Finish action submits identity, never whole-parent status.
 const button={dataset:{action:'finish-action',id:'project'},disabled:false};
 await document.handlers.click({target:{closest:selector=>selector==='[data-action]'?button:null}});
 const finish=writes.find(w=>w.path==='/api/action-complete');assert.deepEqual(finish.body,{id:'project',action_id:'step1'});
 assert.ok(!writes.some(w=>w.path==='/api/item'&&w.body.status==='done'));
 console.log('UI checks passed: Today deduplication/estimate, safe links, unfinished history, backward entry, mode preservation, editing/reordering/Undo, action identity.');
})().catch(e=>{console.error(e);process.exitCode=1;});
