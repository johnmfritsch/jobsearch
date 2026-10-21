/* DOM contract for Apply for me, the desktop key card and Saved answers (backlog 19.2).
   Run with NODE_PATH pointing at jsdom. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates', 'portal.html'), 'utf8');
const portal = fs.readFileSync(path.join(root, 'static', 'portal.js'), 'utf8');
const agentUi = fs.readFileSync(path.join(root, 'static', 'agent_ui.js'), 'utf8');
const line = prefix => {
  const found = portal.split('\n').find(value => value.startsWith(prefix));
  assert(found, `Missing source line: ${prefix}`);
  return found;
};

// Static facts first.
assert(!html.includes('never sent to an AI provider'), 'old privacy sentence is still on the page');
assert(html.includes('shared with your Claude desktop session only when you queue a job'), 'new privacy wording missing');
assert(html.includes('agent_ui.js'), 'agent_ui.js is not loaded by the page');
assert(/startApply\.onclick=\(\)=>applyForJob\(/.test(portal), 'the document-assistant Apply button changed');
assert(/links\.append\(createApplyForMeButton\(jobId\),createApplySessionLink\(\)\)/.test(portal), 'Open apply session is not in the application detail');
assert(!/jppp:/.test(portal), 'the launch URI must live only in agent_ui.js as a constant');

(async () => {
  const dom = new JSDOM(html, {runScripts: 'outside-only', url: 'https://example.test/JobSearch_dev/'});
  const {window} = dom;
  window.assert = assert;
  window.confirm = () => true;
  const setup = `
    const PREFIX='/JobSearch_dev';
    const API='/JobSearch_dev/api/';
    const byId=id=>document.getElementById(id);
    let searchPresets=[{id:2,name:'Dev'},{id:1,name:'Sysadmin'}];
    const notices=[], messages=[]; let applicationsLoaded=0;
    function showNotice(m){notices.push(m);} function showActionMessage(m){messages.push(m);}
    async function loadApplications(){applicationsLoaded++;}
    ${'' /* orderedSearchPresets and the attempt renderer come from the real portal.js */}
    ${line('function orderedSearchPresets()')}
    let applicationDocumentChoices={}, aiProviderCredentials={}, applicationFeatures={preparation_enabled:true,submission_enabled:true};
    function artifactGroups(){return [];} function latestUnanswered(){return [];}
    async function updateDocumentChoice(){} function runApplicationAction(){} function confirmApplicationSubmission(){}
    function saveApplicationAnswers(){} function decideApplicationArtifact(){}
    const fetchCalls=[]; let fetchReplies=[]; let fetchGate=null;
    window.fetch=async(url,options={})=>{
      fetchCalls.push({url,method:options.method||'GET',body:options.body?JSON.parse(options.body):undefined});
      if(fetchGate) await fetchGate;
      const next=fetchReplies.shift()||{status:200,body:{}};
      return {ok:next.status<400,status:next.status,json:async()=>next.body};
    };
  `;
  const body = `
    const buttonsText=root=>[...root.querySelectorAll('button')].map(b=>b.textContent);

    // 1. Apply for me posts the job key (no role) and refreshes the tracker.
    {
      const host=createApplyForMeButton('job-9'); document.body.append(host);
      const button=host.querySelector('button');
      assert.equal(button.textContent,'Apply for me');
      let release; fetchGate=new Promise(r=>{release=r;});
      fetchReplies=[{status:201,body:{success:true,created:true,attempt:{id:'a1'}}}];
      const clicked=button.onclick();
      assert.equal(button.disabled,true,'button must be disabled while the request runs');
      release(); fetchGate=null; await clicked;
      assert.equal(button.disabled,false);
      assert.equal(fetchCalls.at(-1).url,'/JobSearch_dev/api/agent_queue');
      assert.deepEqual(fetchCalls.at(-1).body,{job_key:'job-9'});
      assert.match(notices.at(-1),/Queued for your desktop agent/);
      assert.equal(applicationsLoaded,1);
      host.remove();
    }

    // 2. role_required reveals a picker whose values are default and preset:<id>.
    {
      const host=createApplyForMeButton('job-5'); document.body.append(host);
      fetchReplies=[{status:409,body:{error:'Choose which role to apply under',code:'role_required'}}];
      await host.querySelector('button').onclick();
      const select=host.querySelector('.agent-role-select');
      assert(select,'role picker missing');
      assert.deepEqual([...select.options].map(o=>o.value),['default','preset:2','preset:1']);
      assert.deepEqual([...select.options].map(o=>o.textContent),['Default','Dev','Sysadmin']);
      assert.equal(messages.length,0,'role_required must not toast an error');
      select.value='preset:1';
      fetchReplies=[{status:201,body:{success:true,created:true}}];
      await host.querySelector('.agent-role-picker button').onclick();
      assert.deepEqual(fetchCalls.at(-1).body,{job_key:'job-5',role_key:'preset:1'});
      assert.equal(host.querySelector('.agent-role-picker'),null,'picker should go away once queued');
      host.remove();
    }

    // 3. Any other refusal shows the server text.
    {
      const host=createApplyForMeButton('job-6'); document.body.append(host);
      fetchReplies=[{status:409,body:{error:'Confirm work history for the Dev role first',code:'history_required'}}];
      await host.querySelector('button').onclick();
      assert.equal(messages.at(-1),'Confirm work history for the Dev role first');
      assert.equal(host.querySelector('.agent-role-picker'),null);
      host.remove();
    }

    // 3b. Open apply session is a constant link: no job id, no query, no key.
    {
      const link=createApplySessionLink();
      assert.equal(link.getAttribute('href'),'jppp://claude/jobsearch-apply');
      assert.equal(link.textContent,'Open apply session');
      assert.match(link.title,/bridge is installed/);
      const href=link.getAttribute('href');
      assert(!href.includes('?')&&!href.includes('key')&&!href.replace('jobsearch-apply','').includes('job'),'URI must carry no job, key or query');
      assert.equal(link.getAttribute('target'),null);
    }

    // 4. Attempt detail: the worker buttons are absent for an agent-owned attempt.
    {
      const base={id:'x1',job:{title:'Engineer',company:'Example'},state:'queued_for_agent',state_label:'Queued for agent',
        status_message:'Queued for agent',automation_used:false,source_url:'https://example.test/j',artifacts:[],events:[],answers:{}};
      const needs=['Prepare supported application form','Review and confirm submission','Start a new attempt'];
      for (const state of ['queued_for_agent','ready_for_review','needs_user_input','could_not_fill']) {
        renderApplicationAttemptDetail({...base,state,agent_owned:true,review:state==='ready_for_review'?{summary:'Filled <b>it</b>',filled_fields:[{label:'Name',value:'John'}]}:null});
        const detail=byId('applicationAttemptDetail');
        const shown=buttonsText(detail);
        needs.forEach(text=>assert(!shown.includes(text),state+' must not show '+text));
        assert(!detail.querySelector('#enableAiDocuments'),state+' must not offer AI documents');
        if(state==='ready_for_review'){
          assert.match(detail.textContent,/Review recorded by the agent/);
          assert.match(detail.textContent,/Name: John/);
          assert.equal(detail.querySelector('b'),null,'review text must not be parsed as HTML');
        }
        if(state==='needs_user_input') assert.match(detail.textContent,/Saved answers/);
      }
      renderApplicationAttemptDetail({...base,state:'queued_for_agent',agent_owned:true});
      assert(buttonsText(byId('applicationAttemptDetail')).includes('Cancel attempt'),'Cancel must still be offered');
      renderApplicationAttemptDetail({...base,state:'ready_for_review',agent_owned:false});
      const worker=buttonsText(byId('applicationAttemptDetail'));
      assert(worker.includes('Prepare supported application form'));
      assert(worker.includes('Review and confirm submission'));
    }

    // 5. Desktop key: shown once, never listed, cleared when the panel is hidden.
    {
      fetchReplies=[
        {status:201,body:{success:true,token:'jsa_SECRET',key_id:'k1',label:'My PC',expires_at:'2027-01-01T00:00:00'}},
        {status:200,body:{success:true,agent_api_enabled:true,keys:[{key_id:'k1',label:'My PC',created_at:'2026-10-02T10:00:00',last_used_at:null,expires_at:'2027-01-01T00:00:00'}]}},
      ];
      byId('desktopKeyLabel').value='My PC';
      await createDesktopKey();
      assert.deepEqual(fetchCalls.at(-2).body,{label:'My PC'});
      assert.equal(byId('desktopKeyNew').hidden,false);
      assert.match(byId('desktopKeyNew').textContent,/jsa_SECRET/);
      assert.match(byId('desktopKeyNew').textContent,/desktop_key\.dev/);
      const shown=document.querySelector('.desktop-key-file').textContent;
      assert(shown.startsWith('%LOCALAPPDATA%')&&shown.endsWith('desktop_key.dev'),'card must name the dev file: '+shown);
      assert(!/desktop_key\.prod/.test(byId('desktopKeyNew').textContent),'a DEV page must not name the prod file');
      assert(!byId('desktopKeyList').textContent.includes('jsa_SECRET'),'the list must never show the token');
      assert.match(byId('desktopKeyList').textContent,/My PC/);
      byId('assistantPanel').hidden=false; byId('assistantPanel').hidden=true;
      await new Promise(r=>setTimeout(r,0));
      assert.equal(byId('desktopKeyNew').hidden,true);
      assert(!document.body.textContent.includes('jsa_SECRET'),'the token must leave the page with the panel');
      // Revoke goes through DELETE.
      fetchReplies=[{status:200,body:{success:true}},{status:200,body:{success:true,agent_api_enabled:false,keys:[]}}];
      await byId('desktopKeyList').querySelector('button').onclick?.();
      assert.equal(fetchCalls.at(-2).method,'DELETE');
      assert.match(byId('desktopKeyStatus').textContent,/turned off/);
    }

    // 6. Saved answers render as text and post edits, reuse and deletes.
    {
      fetchReplies=[{status:200,body:{success:true,answers:[
        {id:'q1',question:'Why us? <img src=x onerror=alert(1)>',answer:'Because',field_type:'text',source:'agent_chat',status:'confirmed',safe_to_autofill:false,times_seen:2},
        {id:'q2',question:'Relocate?',answer:'',field_type:'yes_no',source:'agent_chat',status:'needs_answer',safe_to_autofill:false,times_seen:1}]}}];
      await loadSavedAnswers();
      const list=byId('savedAnswersList');
      assert.equal(list.querySelector('img'),null,'question text must not become markup');
      assert.match(list.textContent,/Why us\\? <img/);
      assert.match(list.textContent,/waiting for your answer/);
      const rows=list.querySelectorAll('.saved-answer');
      assert.equal(rows[1].querySelector('.saved-answer-reuse').disabled,true,'cannot reuse an unanswered question');
      rows[0].querySelector('.saved-answer-input').value='Better';
      rows[0].querySelector('.saved-answer-reuse').checked=true;
      fetchReplies=[{status:200,body:{success:true}},{status:200,body:{success:true,answers:[]}}];
      await rows[0].querySelector('button.primary').onclick();
      assert.equal(fetchCalls.at(-2).url,'/JobSearch_dev/api/question_bank/q1');
      assert.deepEqual(fetchCalls.at(-2).body,{answer:'Better',safe_to_autofill:true});
      assert.match(list.textContent,/Nothing saved yet/);
    }
  `;
  await window.eval(`${setup}
${line('function renderApplicationAttemptDetail(')}
${agentUi}
(async()=>{
${body}
})().then(()=>window.__done=true,e=>{window.__error=e;})`);
  await new Promise(resolve => setTimeout(resolve, 200));
  if (window.__error) throw window.__error;
  assert(window.__done, 'UI checks did not finish');
  console.log('agent UI: ok');
})().catch(error => { console.error(error); process.exit(1); });
