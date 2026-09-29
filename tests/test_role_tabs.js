/* DOM contract for the role folders. Run with NODE_PATH pointing at jsdom. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates', 'portal.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'static', 'portal.js'), 'utf8');
const part = (start, end) => {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert(from >= 0 && to > from, `Missing source section: ${start}`);
  return source.slice(from, to);
};
const line = prefix => {
  const found = source.split('\n').find(value => value.startsWith(prefix));
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
    document.body.insertAdjacentHTML('beforeend','<textarea id="keywordBank"></textarea>');
    let identity={profile_key:'test'},config={active_search_preset_name:'HL7 integration'},
      searchPresets=[{id:3,name:'Vibe Coder',color:'#2563eb'},
        {id:1,name:'HL7 integration',color:'#16a34a'},
        {id:2,name:'System Admin',color:'#b45309'}],
      applicationRoleTab='all',applicationRecords={},resultJobs=[],
      applicationFilters={work_style:'',minimum_score:'',salary:'',source:'',stage:'',posted:''},
      savedCriteriaForm=null,savedSnapshot={config:{},resume:''},presetSwitchPending=false;
    const clone=value=>JSON.parse(JSON.stringify(value));
    function safeRoleColor(value){return /^#[0-9a-f]{6}$/i.test(String(value||''))?value:'#64748b';}
    function searchPresetByName(name){return searchPresets.find(item=>item.name.toLowerCase()===String(name||'').toLowerCase())||null;}
    function activeSearchPreset(){return searchPresetByName(config.active_search_preset_name);}
    function defaultPriorityEmployerRoles(){return [];} function renderPriorityEmployerRoleList(){}
    async function loadSetupRoleDocuments(){} async function loadPriorityEmployers(){}
    let editingEmployerSourceId=null;
    function enrichApplicationRecord(id,record){return record;}
    function resultForApplication(){return {};}
    function updateApplicationSources(){}
    function hasScore(job){return job.score!=null;}
    let apiCalls=0,apiError=null;
    async function api(){apiCalls++;if(apiError)throw apiError;return {preset:{id:2,name:'System Admin',config:{active_search_preset_name:'System Admin'},resume:'saved resume'}};}
    function fillCriteria(cfg,resume){config=clone(cfg);byId('resume').value=resume;renderSearchPresets();}
    async function fetchSerpapiStatus(){}
  `;
  const functions = [
    part('function orderedSearchPresets()', 'function currentRoleQuery()'),
    line('function stageLabel('), line('function trackerStage('),
    line('function filteredApplications('), line('function renderApplications('),
    part('async function loadSearchPreset(', 'async function deleteActiveSearchPreset()'),
  ].join('\n');
  await window.eval(`${setup}\n${functions}\n(async()=>{
    applicationRecords={
      a:{stage:'rejected',title:'HL7 role',search_role:'hl7 INTEGRATION',source:'Manual entry'},
      b:{stage:'saved',title:'Admin role',search_role:'System Admin'},
      c:{stage:'closed',title:'Orphan role',search_role:'Deleted role'},
      d:{stage:'applied',title:'No role',search_role:'N/A'},
    };
    renderApplications();
    assert.deepEqual([...byId('applicationRoleTabs').children].map(tab=>tab.textContent),
      ['All','HL7 integration','System Admin','Vibe Coder']);
    assert.equal(byId('applications').children.length,4);
    assert.equal(byId('applicationRoleTabs-tab-all').getAttribute('aria-selected'),'true');
    byId('applicationRoleTabs-tab-all').focus();
    byId('applicationRoleTabs-tab-all').dispatchEvent(new KeyboardEvent('keydown',
      {key:'End',bubbles:true}));
    assert.equal(applicationRoleTab,'3');
    byId('applicationRoleTabs-tab-all').click();
    byId('applicationRoleTabs-tab-1').click();
    assert.equal(byId('applications').children.length,1);
    assert.equal(byId('applications').firstElementChild.querySelector('.application-stage-badge').textContent,'Rejected');
    assert.equal(byId('applicationRoleSheet').getAttribute('aria-labelledby'),'applicationRoleTabs-tab-1');
    applicationFilters.stage='saved';renderApplications();
    assert.equal(byId('applications').querySelectorAll('.application').length,0);
    applicationFilters.stage='';renderApplications();
    assert.equal(byId('applications').querySelectorAll('.application').length,1);
    applicationRecords.a.search_role='System Admin';renderApplications();
    assert.equal(byId('applications').querySelectorAll('.application').length,0);
    applicationRecords.a.search_role='hl7 INTEGRATION';renderApplications();
    searchPresets=searchPresets.filter(role=>role.id!==1);renderApplications();
    assert.equal(applicationRoleTab,'all');
    assert.equal(byId('applications').querySelectorAll('.application').length,4);
    searchPresets.push({id:1,name:'HL7 integration',color:'#16a34a'});
    renderSearchPresets();
    assert.deepEqual([...byId('searchPresetButtons').children].map(tab=>tab.textContent),
      ['HL7 integration','System Admin','Vibe Coder']);
    assert.equal(byId('searchPresetButtons-tab-1').getAttribute('aria-selected'),'true');
    byId('keywords').value='unsaved';markCriteriaSaved();
    byId('keywords').value='changed';
    window.confirm=()=>false;
    assert.equal(await loadSearchPreset(2),false);
    assert.equal(config.active_search_preset_name,'HL7 integration');
    assert.equal(byId('keywords').value,'changed');
    assert.equal(apiCalls,0);
    window.confirm=()=>true;
    apiError=new Error('Load failed');
    assert.equal(await loadSearchPreset(2),false);
    assert.equal(byId('searchPresetButtons-tab-1').getAttribute('aria-selected'),'true');
    assert.equal(config.active_search_preset_name,'HL7 integration');
    apiError=null;
    assert.equal(await loadSearchPreset(2),true,byId('searchPresetMessage').textContent);
    assert.equal(config.active_search_preset_name,'System Admin');
    assert.equal(byId('searchPresetButtons-tab-2').getAttribute('aria-selected'),'true');
    assert.equal(byId('resume').value,'saved resume');
    searchPresets=[];renderSearchPresets();
    assert.equal(byId('searchPresetButtons').hidden,true);
    assert.equal(byId('setupRoleEmpty').hidden,false);
  })()`);
  console.log('Role tab DOM checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
