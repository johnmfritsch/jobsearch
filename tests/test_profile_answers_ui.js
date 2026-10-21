/* DOM contract for the profile answers and role history UI (backlog 19.1).
   Run with NODE_PATH pointing at jsdom. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates', 'portal.html'), 'utf8');
const portal = fs.readFileSync(path.join(root, 'static', 'portal.js'), 'utf8');
const answers = fs.readFileSync(path.join(root, 'static', 'profile_answers.js'), 'utf8');
const part = (start, end) => {
  const from = portal.indexOf(start), to = portal.indexOf(end, from);
  assert(from >= 0 && to > from, `Missing source section: ${start}`);
  return portal.slice(from, to);
};
const line = prefix => {
  const found = portal.split('\n').find(value => value.startsWith(prefix));
  assert(found, `Missing source line: ${prefix}`);
  return found;
};

(async () => {
  const dom = new JSDOM(html, {runScripts: 'outside-only', url: 'https://example.test/JobSearch_dev/'});
  const {window} = dom;
  window.assert = assert;
  window.confirm = () => true;
  const setup = `
    const byId=id=>document.getElementById(id);
    let searchPresets=[{id:1,name:'Sysadmin',color:'#16a34a'},{id:2,name:'Dev',color:'#2563eb'}],
      config={active_search_preset_name:'Sysadmin'};
    function searchPresetByName(name){return searchPresets.find(p=>p.name.toLowerCase()===String(name||'').toLowerCase())||null;}
    function activeSearchPreset(){return searchPresetByName(config.active_search_preset_name);}
    function safeRoleColor(v){return /^#[0-9a-f]{6}$/i.test(String(v||''))?v:'#64748b';}
    const calls=[]; let replies=[];
    async function api(endpoint,body){calls.push([endpoint,body]);const next=replies.shift();if(next instanceof Error)throw next;return next;}
    function openWorkspace(){} async function loadApplicationAssistant(){await initRoleHistory();}
  `;
  const portalParts = [
    part('function orderedSearchPresets()', 'function renderFolderTabs('),
    part('function renderFolderTabs(', 'function renderApplicationRoleTabs()'),
    line('const applicationProfileFields='),
    line('function fillApplicationProfile('),
    line('function guessNameParts('),
    line('function collectApplicationProfile('),
  ].join('\n');
  const chunks = [];
  const run = code => { chunks.push(code); };
  const work = (over = {}) => ({id: 'w1', title: 'Admin', employer: 'Acme', location: '', from: '01/2015',
    to: '06/2018', current: false, description: '', ...over});

  // 1. Profile sections: fill, disable until opted in, collect.
  await run(`
    fillApplicationProfile({full_name:'A',middle_name:'M',
      screening:{us_person:'yes',shift_availability:'Any'},
      eeo:{opt_in:true,gender:'male',veteran:'not_a_veteran'}});
    assert.equal(byId('applicationMiddleName').value,'M');
    assert.equal(byId('screenUsPerson').value,'yes');
    assert.equal(byId('screenCurrentEmployee').value,'');
    assert.equal(byId('eeoOptIn').checked,true);
    assert.equal(byId('eeoGender').disabled,false);
    assert.equal(byId('eeoGender').value,'male');
    const collected=collectApplicationProfile();
    assert.equal(collected.middle_name,'M');
    assert.equal(collected.screening.us_person,'yes');
    assert.equal(collected.eeo.gender,'male');
    // First / middle / last: stored parts show as they are; an older full name is split and flagged.
    fillApplicationProfile({first_name:'Mary Jo',middle_name:'',last_name:'Smith',full_name:'Mary Jo Smith'});
    assert.equal(byId('applicationFirstName').value,'Mary Jo');
    assert.equal(byId('applicationLastName').value,'Smith');
    assert.equal(collectApplicationProfile().first_name,'Mary Jo');
    assert.equal('full_name' in collectApplicationProfile(),false);
    byId('applicationProfileMessage').textContent='';
    fillApplicationProfile({full_name:'John M. Fritsch'});
    assert.equal(byId('applicationFirstName').value,'John');
    assert.equal(byId('applicationMiddleName').value,'M.');
    assert.equal(byId('applicationLastName').value,'Fritsch');
    assert.match(byId('applicationProfileMessage').textContent,/split from the full name/);
    fillApplicationProfile({full_name:'A',middle_name:'M',
      screening:{us_person:'yes',shift_availability:'Any'},
      eeo:{opt_in:true,gender:'male',veteran:'not_a_veteran'}});
    byId('eeoOptIn').checked=false;byId('eeoOptIn').onchange();
    assert.equal(byId('eeoGender').disabled,true);
    assert.deepEqual(collectApplicationProfile().eeo,{opt_in:false});
    assert.match(byId('applicationProfileMessage').textContent,/deletes your stored/);
    fillApplicationProfile({});
    assert.equal(byId('screenAge18').value,'yes');
    assert.equal(byId('screenUsPerson').value,'yes');
    assert.equal(byId('screenCurrentEmployee').value,'');
    fillApplicationProfile({screening:{age_18_plus:'',us_person:'no'}});
    assert.equal(byId('screenAge18').value,'');
    assert.equal(byId('screenUsPerson').value,'no');
    fillApplicationProfile({});
    assert.equal(byId('eeoOptIn').checked,false);
    assert.equal(byId('eeoGender').disabled,true);
    assert.deepEqual(collectApplicationProfile().eeo,{opt_in:false});
  `);

  // 2. Tabs follow the active role; rows render; date edit unticks verified.
  await run(`
    replies=[{history:{work_history:[${JSON.stringify(work())}],education:[],verified_at:'2026-10-02',has_previous:true,warnings:['Check dates']}}];
    await initRoleHistory();
    assert.deepEqual([...byId('historyRoleTabs').children].map(t=>t.textContent),['Dev','Sysadmin']);
    assert.equal(historyRole,'1');
    assert.equal(calls.at(-1)[0],'role_history?preset_id=1');
    assert.equal(byId('historyWorkRows').querySelectorAll('.history-row').length,1);
    assert.equal(byId('historyVerified').checked,true);
    assert.equal(byId('historyUndo').hidden,false);
    assert.equal(byId('historyWarnings').hidden,false);
    const start=byId('historyWorkRows').querySelectorAll('input')[3];
    start.value='02/2015';start.oninput();
    assert.equal(byId('historyVerified').checked,false);
    assert.equal(historyState.dirty,true);
  `);

  // 3. Save posts the edited rows with ids and the verified flag, and surfaces server errors.
  await run(`
    replies=[new Error('Work history row 1 (Acme): start date must be MM/YYYY')];
    await saveRoleHistory();
    const sent=calls.at(-1);
    assert.equal(sent[0],'role_history');
    assert.equal(sent[1].preset_id,1);
    assert.equal(sent[1].history.work_history[0].id,'w1');
    assert.equal(sent[1].history.work_history[0].from,'02/2015');
    assert.equal(sent[1].history.verified,false);
    assert.equal(sent[1].history.work_history[0].status,undefined);
    assert.match(byId('historyMessage').textContent,/start date must be MM\\/YYYY/);
    assert.equal(historyState.dirty,true);
  `);

  // 4. Typed MMYYYY is normalised; add/remove/move rows; current disables the end date.
  await run(`
    historyState.work=[${JSON.stringify(work())},${JSON.stringify(work({id: 'w2', employer: 'Beta'}))}];
    renderHistory();
    const first=byId('historyWorkRows').querySelectorAll('input')[3];
    first.value='042019';first.onblur();
    assert.equal(historyState.work[0].from,'04/2019');
    byId('historyAddJob').onclick();
    assert.equal(historyState.work.length,3);
    assert.equal(historyState.work[2].id,'');
    const rows=()=>[...byId('historyWorkRows').querySelectorAll('.history-row')];
    rows()[1].querySelector('.row-actions button').click();
    assert.deepEqual(historyState.work.map(r=>r.employer),['Beta','Acme','']);
    rows()[2].querySelectorAll('.row-actions button')[2].click();
    assert.equal(historyState.work.length,2);
    const check=rows()[0].querySelector('.assistant-check input');
    check.checked=true;check.onchange();
    assert.equal(historyState.work[0].current,true);
    assert.equal(historyState.work[0].to,'');
    assert.equal(rows()[0].querySelectorAll('input')[4].disabled,true);
  `);

  // 5. Copy preview loads editable rows with status badges; nothing is posted until Save.
  await run(`
    replies=[{preview:{mode:'merge',source_role:'Dev',will_remove:{work_history:0,education:0},
      work_history:[${JSON.stringify(work({status: 'kept'}))},
        ${JSON.stringify(work({id: 'n1', employer: 'Gamma', status: 'new', copied_from: 'Dev'}))},
        ${JSON.stringify(work({id: 'c1', status: 'conflict', to: '07/2018', copied_from: 'Dev'}))}],
      education:[]}}];
    byId('historyCopy').onclick();
    assert.equal(byId('historyCopyPanel').hidden,false);
    assert.deepEqual([...byId('historyCopySource').options].map(o=>o.textContent),['Dev']);
    const before=calls.length;
    byId('historyCopyPreview').onclick();
    await new Promise(r=>setTimeout(r,0));
    assert.equal(calls.length,before+1);
    assert.equal(calls.at(-1)[0],'role_history/copy_preview');
    assert.deepEqual({m:calls.at(-1)[1].mode,s:calls.at(-1)[1].source_preset_id,t:calls.at(-1)[1].preset_id},{m:'merge',s:2,t:1});
    assert.equal(byId('historyBanner').hidden,false);
    assert.match(byId('historyBanner').textContent,/Nothing is saved until/);
    assert.equal(byId('historyWorkRows').querySelectorAll('.history-row').length,3);
    assert.equal(byId('historyWorkRows').querySelectorAll('.history-badge.conflict').length,1);
    assert.equal(byId('historyWorkRows').querySelectorAll('.history-badge.new').length,1);
    assert.equal(historyState.verified,false);
    assert.equal(byId('historyDiscard').hidden,false);
    assert.equal(byId('historyCopyPanel').hidden,true);
  `);

  // 6. Import from resume appends drafts, skipping jobs already present, flagged as drafts.
  await run(`
    historyState.work=[${JSON.stringify(work())}];
    replies=[{draft:{work_history:[${JSON.stringify(work({id: 'd0'}))},
      ${JSON.stringify(work({id: 'd1', employer: 'Gamma', from: '', issues: ['start month missing (resume shows 2010)']}))}],
      education:[{id:'e1',level:'master',school:'Cedar Crest',degree:'MBA',graduated:'2009',issues:['check school, degree and field']}]}}];
    await importHistoryFromResume();
    assert.deepEqual(historyState.work.map(r=>r.employer),['Acme','Gamma']);
    assert.equal(historyState.education.length,1);
    assert.equal(historyState.fromDraft,true);
    assert.match(byId('historyWorkRows').textContent,/start month missing/);
    assert.match(byId('historyBanner').textContent,/drafts/);
    replies=[{history:{work_history:[],education:[],verified_at:''}}];
    await saveRoleHistory();
    assert.equal(calls.at(-1)[1].source,'resume_draft');
  `);

  // 7. Switching roles with unsaved changes asks first; undo applies the server copy.
  await run(`
    historyState.dirty=true;
    let asked=0;window.confirm=()=>{asked++;return false;};
    await switchHistoryRole('2');
    assert.equal(asked,1);assert.equal(historyRole,'1');
    window.confirm=()=>true;
    replies=[{history:{work_history:[],education:[],verified_at:''}}];
    await switchHistoryRole('2');
    assert.equal(historyRole,'2');
    assert.equal(calls.at(-1)[0],'role_history?preset_id=2');
    replies=[{history:{work_history:[${JSON.stringify(work())}],education:[],verified_at:'',has_previous:false}}];
    await undoRoleHistory();
    assert.equal(calls.at(-1)[0],'role_history/undo');
    assert.equal(historyState.work.length,1);
  `);

  // 8. Role creation: start-from options list existing roles, clean first.
  await run(`
    fillStartFromOptions();
    assert.deepEqual([...byId('searchPresetStartFrom').options].map(o=>o.textContent),
      ['Start clean','Start from Dev','Start from Sysadmin']);
    assert.equal(byId('searchPresetStartFrom').value,'');
  `);

  await window.eval(`${setup}
${portalParts}
${answers}
(async()=>{
${chunks.map(c => '{' + c + '}').join(String.fromCharCode(10))}
})()`);
  console.log('profile answers UI: ok');
})().catch(error => { console.error(error); process.exit(1); });
