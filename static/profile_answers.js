// Application profile answers (backlog 19.1): account-level screening and
// voluntary self-identification, plus per-role work history and education.
// Loaded after portal.js and shares its globals (byId, api, renderFolderTabs,
// orderedSearchPresets, searchPresets, activeSearchPreset, openWorkspace).

const YES_NO_OPTIONS = [['', 'Ask me each time'], ['yes', 'Yes'], ['no', 'No']];
const SCREENING_FIELDS = {
  current_employee: 'screenCurrentEmployee', former_employee: 'screenFormerEmployee',
  age_18_plus: 'screenAge18', restrictive_agreements: 'screenRestrictive',
  us_person: 'screenUsPerson', conflict_of_interest: 'screenConflict',
};
const EEO_ASK = ['', 'Ask me each time'];
const EEO_FIELDS = {
  gender: ['eeoGender', [EEO_ASK, ['male', 'Male'], ['female', 'Female'], ['non_binary', 'Non-binary'],
    ['decline', 'I choose not to answer']]],
  race_ethnicity: ['eeoRace', [EEO_ASK, ['hispanic_or_latino', 'Hispanic or Latino'], ['white', 'White'],
    ['black_or_african_american', 'Black or African American'], ['asian', 'Asian'],
    ['native_hawaiian_or_pacific_islander', 'Native Hawaiian or Other Pacific Islander'],
    ['american_indian_or_alaska_native', 'American Indian or Alaska Native'],
    ['two_or_more', 'Two or more races'], ['decline', 'I choose not to answer']]],
  veteran: ['eeoVeteran', [EEO_ASK, ['not_a_veteran', 'I am not a veteran'], ['protected_veteran', 'Protected veteran'],
    ['veteran_not_protected', 'Veteran, not a protected veteran'], ['decline', 'I choose not to answer']]],
  disability: ['eeoDisability', [EEO_ASK, ['no', 'No'], ['yes', 'Yes'], ['decline', 'I choose not to answer']]],
};
const EDU_LEVEL_OPTIONS = [['', 'Level…'], ['high_school', 'High school'], ['associate', 'Associate'],
  ['bachelor', "Bachelor's"], ['master', "Master's"], ['doctorate', 'Doctorate'], ['other', 'Other']];

function fillSelectOptions(select, options) {
  select.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
}

function initProfileSectionOptions() {
  if (initProfileSectionOptions.done) return;
  Object.values(SCREENING_FIELDS).forEach(id => fillSelectOptions(byId(id), YES_NO_OPTIONS));
  Object.values(EEO_FIELDS).forEach(([id, options]) => fillSelectOptions(byId(id), options));
  byId('eeoOptIn').onchange = syncEeoEnabled;
  initProfileSectionOptions.done = true;
}

function syncEeoEnabled() {
  const on = byId('eeoOptIn').checked;
  Object.values(EEO_FIELDS).forEach(([id]) => { byId(id).disabled = !on; });
  const anyStored = Object.values(EEO_FIELDS).some(([id]) => byId(id).value);
  if (!on && anyStored) {
    byId('applicationProfileMessage').textContent =
      'Saving with this turned off deletes your stored self-identification answers.';
  }
}

// Preselected only when screening answers have never been saved. Once saved, an
// empty answer means "Ask me each time" and is respected.
const SCREENING_DEFAULTS = {age_18_plus: 'yes', us_person: 'yes'};

function fillProfileSections(profile) {
  initProfileSectionOptions();
  const screening = profile.screening || SCREENING_DEFAULTS;
  Object.entries(SCREENING_FIELDS).forEach(([key, id]) => { byId(id).value = screening[key] || ''; });
  byId('screenShift').value = screening.shift_availability || '';
  byId('screenNotes').value = screening.notes || '';
  const eeo = profile.eeo || {};
  byId('eeoOptIn').checked = eeo.opt_in === true;
  Object.entries(EEO_FIELDS).forEach(([key, [id]]) => { byId(id).value = eeo[key] || ''; });
  syncEeoEnabled();
  byId('applicationProfileMessage').textContent = '';
}

function collectScreening() {
  const screening = {};
  Object.entries(SCREENING_FIELDS).forEach(([key, id]) => { screening[key] = byId(id).value; });
  screening.shift_availability = byId('screenShift').value.trim();
  screening.notes = byId('screenNotes').value.trim();
  return screening;
}

function collectEeo() {
  const on = byId('eeoOptIn').checked;
  const eeo = {opt_in: on};
  if (on) Object.entries(EEO_FIELDS).forEach(([key, [id]]) => { eeo[key] = byId(id).value; });
  return eeo;
}

// ── Role-level work history and education ────────────────────────────────────

let historyRole = 'default';
let historyState = {work: [], education: [], verified: false, verifiedAt: '', dirty: false,
  previewing: false, hasPrevious: false, fromDraft: false};

function historyRoleItems() {
  const items = orderedSearchPresets().map(preset => ({key: String(preset.id), label: preset.name, color: preset.color}));
  if (!searchPresets.length || !activeSearchPreset()) items.unshift({key: 'default', label: 'Default', color: '#64748b'});
  return items;
}
function historyRoleLabel(key) {
  const item = historyRoleItems().find(entry => entry.key === String(key));
  return item ? item.label : 'this role';
}
function historyRoleBody(key = historyRole, prefix = '') {
  return String(key) === 'default' ? {[prefix + 'role_name']: 'Default'} : {[prefix + 'preset_id']: Number(key)};
}
function renderHistoryRoleTabs() {
  renderFolderTabs(byId('historyRoleTabs'), historyRoleItems(), historyRole, byId('historyRoleSheet'),
    key => switchHistoryRole(String(key)));
}

async function initRoleHistory() {
  const items = historyRoleItems();
  if (!items.some(item => item.key === historyRole)) {
    const active = activeSearchPreset();
    historyRole = active ? String(active.id) : (items[0] ? items[0].key : 'default');
  }
  renderHistoryRoleTabs();
  await loadRoleHistory();
}

async function switchHistoryRole(key) {
  if (key === historyRole) return;
  if (historyState.dirty && !confirm('Discard the unsaved changes to this role\'s work history?')) {
    renderHistoryRoleTabs();
    return;
  }
  historyRole = key;
  renderHistoryRoleTabs();
  hideHistoryCopyPanel();
  await loadRoleHistory();
}

async function loadRoleHistory() {
  byId('historyMessage').textContent = '';
  try {
    const query = new URLSearchParams(historyRoleBody());
    const response = await api('role_history?' + query.toString());
    applyHistory(response.history);
  } catch (error) {
    byId('historyMessage').textContent = error.message || 'Could not load work history.';
  }
}

function applyHistory(history, banner = '') {
  historyState = {
    work: (history.work_history || []).map(row => ({...row})),
    education: (history.education || []).map(row => ({...row})),
    verified: !!history.verified_at, verifiedAt: history.verified_at || '',
    dirty: false, previewing: false, hasPrevious: !!history.has_previous, fromDraft: false,
  };
  renderHistory();
  renderHistoryWarnings(history.warnings || []);
  setHistoryBanner(banner);
}

function setHistoryBanner(text) {
  const banner = byId('historyBanner');
  banner.textContent = text || '';
  banner.hidden = !text;
  byId('historyDiscard').hidden = !(historyState.dirty || historyState.previewing);
}
function renderHistoryWarnings(warnings) {
  const list = byId('historyWarnings');
  list.replaceChildren(...warnings.map(text => { const item = document.createElement('li'); item.textContent = text; return item; }));
  list.hidden = !warnings.length;
}
function markHistoryDirty() {
  historyState.dirty = true;
  byId('historyDiscard').hidden = false;
}
function dateEdited() {
  historyState.verified = false;
  byId('historyVerified').checked = false;
  updateVerifiedNote();
  markHistoryDirty();
}
function updateVerifiedNote() {
  byId('historyVerifiedNote').textContent = historyState.verified && historyState.verifiedAt && !historyState.dirty
    ? `Marked as checked on ${historyState.verifiedAt}.`
    : 'Not marked as checked yet. Dates read from a resume are often only a year, and forms need the month.';
}

function normalizeMonthYear(value) {
  const text = String(value || '').trim();
  let match = text.match(/^(\d{2})(\d{4})$/);
  if (match) return `${match[1]}/${match[2]}`;
  match = text.match(/^(\d)\/(\d{4})$/);
  if (match) return `0${match[1]}/${match[2]}`;
  return text;
}

function historyField(label, value, onInput, options = {}) {
  const wrapper = document.createElement('label');
  if (options.wide) wrapper.className = 'wide';
  wrapper.append(label);
  let control;
  if (options.select) {
    control = document.createElement('select');
    fillSelectOptions(control, options.select);
  } else if (options.textarea) {
    control = document.createElement('textarea');
  } else {
    control = document.createElement('input');
  }
  if (options.maxLength) control.maxLength = options.maxLength;
  if (options.placeholder) control.placeholder = options.placeholder;
  control.value = value || '';
  control.disabled = !!options.disabled;
  control.oninput = () => onInput(control.value, control);
  if (options.onBlur) control.onblur = () => options.onBlur(control);
  wrapper.append(control);
  return wrapper;
}

function historyButton(text, onClick, disabled = false) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary';
  button.textContent = text;
  button.disabled = disabled;
  button.onclick = onClick;
  return button;
}

function rowStatusNote(row) {
  const wrap = document.createElement('div');
  wrap.className = 'row-issues';
  const parts = [];
  if (row.status === 'new') parts.push(['new', `Added from ${row.copied_from || 'another role'}`]);
  else if (row.status === 'conflict') parts.push(['conflict', `Same job as one already here but different dates (from ${row.copied_from || 'another role'}). Keep the right one and remove the other.`]);
  else if (row.status === 'kept') parts.push(['kept', 'Already in this role']);
  else if (row.status === 'draft') parts.push(['draft', 'Draft read from the resume. Check it.']);
  (row.issues || []).forEach(issue => parts.push(['issue', issue]));
  if (!parts.length) return null;
  parts.forEach(([kind, text]) => {
    const line = document.createElement('div');
    if (kind !== 'issue') {
      const badge = document.createElement('span');
      badge.className = 'history-badge ' + kind;
      badge.textContent = kind === 'conflict' ? 'Conflict' : kind === 'new' ? 'New' : kind === 'kept' ? 'Existing' : 'Draft';
      line.append(badge, ' ');
    }
    line.append(text);
    wrap.append(line);
  });
  return wrap;
}

function moveRow(list, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
  markHistoryDirty();
  renderHistory();
}

function renderHistory() {
  const workRoot = byId('historyWorkRows'), eduRoot = byId('historyEduRows');
  workRoot.replaceChildren();
  eduRoot.replaceChildren();
  historyState.work.forEach((row, index) => {
    const box = document.createElement('div');
    box.className = 'history-row' + (row.status ? ' is-' + row.status : '');
    const toField = historyField('End (MM/YYYY)', row.to, value => { row.to = value; dateEdited(); },
      {maxLength: 7, placeholder: '04/2025', disabled: !!row.current, onBlur: control => { row.to = control.value = normalizeMonthYear(control.value); }});
    box.append(
      historyField('Job title', row.title, value => { row.title = value; markHistoryDirty(); }, {maxLength: 160}),
      historyField('Employer as the application form shows it', row.employer, value => { row.employer = value; markHistoryDirty(); }, {maxLength: 200}),
      historyField('Location', row.location, value => { row.location = value; markHistoryDirty(); }, {maxLength: 160, placeholder: 'City, ST'}),
      historyField('Start (MM/YYYY)', row.from, value => { row.from = value; dateEdited(); },
        {maxLength: 7, placeholder: '10/2018', onBlur: control => { row.from = control.value = normalizeMonthYear(control.value); }}),
      toField);
    const current = document.createElement('label');
    current.className = 'assistant-check';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = !!row.current;
    check.onchange = () => {
      row.current = check.checked;
      if (row.current) row.to = '';
      dateEdited();
      renderHistory();
    };
    current.append(check, ' I still work here');
    box.append(current,
      historyField('What you did (optional)', row.description, value => { row.description = value; markHistoryDirty(); },
        {textarea: true, wide: true, maxLength: 2000}));
    const note = rowStatusNote(row);
    if (note) box.append(note);
    const actions = document.createElement('div');
    actions.className = 'row-actions';
    actions.append(
      historyButton('Move up', () => moveRow(historyState.work, index, -1), index === 0),
      historyButton('Move down', () => moveRow(historyState.work, index, 1), index === historyState.work.length - 1),
      historyButton('Remove', () => { historyState.work.splice(index, 1); dateEdited(); renderHistory(); }));
    box.append(actions);
    workRoot.append(box);
  });
  historyState.education.forEach((row, index) => {
    const box = document.createElement('div');
    box.className = 'history-row' + (row.status ? ' is-' + row.status : '');
    box.append(
      historyField('Level', row.level, value => { row.level = value; markHistoryDirty(); }, {select: EDU_LEVEL_OPTIONS}),
      historyField('School', row.school, value => { row.school = value; markHistoryDirty(); }, {maxLength: 200}),
      historyField('Degree', row.degree, value => { row.degree = value; markHistoryDirty(); }, {maxLength: 160, placeholder: 'MBA'}),
      historyField('Field of study', row.field, value => { row.field = value; markHistoryDirty(); }, {maxLength: 160}),
      historyField('Location', row.location, value => { row.location = value; markHistoryDirty(); }, {maxLength: 160}),
      historyField('Graduated (YYYY or MM/YYYY)', row.graduated, value => { row.graduated = value; markHistoryDirty(); },
        {maxLength: 7, placeholder: '2009'}));
    const note = rowStatusNote(row);
    if (note) box.append(note);
    const actions = document.createElement('div');
    actions.className = 'row-actions';
    actions.append(
      historyButton('Move up', () => moveRow(historyState.education, index, -1), index === 0),
      historyButton('Move down', () => moveRow(historyState.education, index, 1), index === historyState.education.length - 1),
      historyButton('Remove', () => { historyState.education.splice(index, 1); markHistoryDirty(); renderHistory(); }));
    box.append(actions);
    eduRoot.append(box);
  });
  if (!historyState.work.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No jobs saved for this role yet. Import them from the resume, copy them from another role, or add them by hand.';
    workRoot.append(empty);
  }
  byId('historyVerified').checked = historyState.verified;
  byId('historyUndo').hidden = !historyState.hasPrevious;
  updateVerifiedNote();
}

function historyPayload() {
  const keep = (row, fields) => {
    const out = {};
    fields.forEach(field => { out[field] = row[field]; });
    if (row.copied_from) out.copied_from = row.copied_from;
    return out;
  };
  return {
    work_history: historyState.work.map(row => keep(row, ['id', 'title', 'employer', 'location', 'from', 'to', 'current', 'description'])),
    education: historyState.education.map(row => keep(row, ['id', 'level', 'school', 'degree', 'field', 'location', 'graduated'])),
    verified: byId('historyVerified').checked,
  };
}

async function saveRoleHistory() {
  const message = byId('historyMessage'), button = byId('saveRoleHistory');
  button.disabled = true;
  try {
    const response = await api('role_history', {
      ...historyRoleBody(), history: historyPayload(),
      source: historyState.fromDraft ? 'resume_draft' : 'manual'});
    applyHistory(response.history);
    message.textContent = 'Work history saved.';
  } catch (error) {
    message.textContent = error.message || 'Could not save work history.';
  } finally {
    button.disabled = false;
  }
}

async function importHistoryFromResume() {
  const message = byId('historyMessage');
  message.textContent = '';
  try {
    const response = await api('role_history/import_resume', historyRoleBody());
    const have = new Set(historyState.work.map(row => `${row.employer}|${row.title}`.toLowerCase()));
    const fresh = (response.draft.work_history || []).filter(row => !have.has(`${row.employer}|${row.title}`.toLowerCase()));
    const haveSchools = new Set(historyState.education.map(row => `${row.school}|${row.level}`.toLowerCase()));
    const freshEdu = (response.draft.education || []).filter(row => !haveSchools.has(`${row.school}|${row.level}`.toLowerCase()));
    if (!fresh.length && !freshEdu.length) {
      message.textContent = 'The resume produced nothing new to add. You can still add entries by hand.';
      return;
    }
    historyState.work.push(...fresh.map(row => ({...row, id: ''})));
    historyState.education.push(...freshEdu.map(row => ({...row, id: ''})));
    historyState.fromDraft = true;
    historyState.verified = false;
    historyState.previewing = true;
    markHistoryDirty();
    renderHistory();
    setHistoryBanner(`${fresh.length} job${fresh.length === 1 ? '' : 's'} and ${freshEdu.length} education entr${freshEdu.length === 1 ? 'y' : 'ies'} were read from this role's resume. They are drafts: check every entry, fix the items shown under it, then press Save work history. Nothing is saved yet.`);
  } catch (error) {
    message.textContent = error.message || 'Could not read the resume.';
  }
}

function hideHistoryCopyPanel() { byId('historyCopyPanel').hidden = true; }

function showHistoryCopyPanel() {
  const select = byId('historyCopySource');
  const others = historyRoleItems().filter(item => item.key !== historyRole);
  const allRoles = others.length ? others : [];
  if (!allRoles.length) {
    byId('historyMessage').textContent = 'There is no other role to copy from yet.';
    return;
  }
  select.replaceChildren(...allRoles.map(item => new Option(item.label, item.key)));
  byId('historyCopyPanel').hidden = false;
}

async function previewHistoryCopy(sourceKey, mode) {
  const message = byId('historyMessage');
  message.textContent = '';
  try {
    const response = await api('role_history/copy_preview', {
      ...historyRoleBody(), ...historyRoleBody(sourceKey, 'source_'), mode});
    const preview = response.preview;
    historyState.work = preview.work_history.map(row => ({...row}));
    historyState.education = preview.education.map(row => ({...row}));
    historyState.verified = false;
    historyState.previewing = true;
    historyState.fromDraft = false;
    markHistoryDirty();
    renderHistory();
    const lost = preview.will_remove.work_history + preview.will_remove.education;
    setHistoryBanner(mode === 'replace'
      ? `Previewing a replacement from ${preview.source_role}. ${lost ? `Saving replaces this role's ${preview.will_remove.work_history} job(s) and ${preview.will_remove.education} education entr${preview.will_remove.education === 1 ? 'y' : 'ies'}; Undo last save brings them back. ` : ''}Change, add or remove anything below. Nothing is saved until you press Save work history.`
      : `Previewing a merge from ${preview.source_role}. New entries will be added. A Conflict is a job already in this role with different dates: keep the one that is right and remove the other. Nothing is saved until you press Save work history.`);
    hideHistoryCopyPanel();
  } catch (error) {
    message.textContent = error.message || 'Could not build the preview.';
  }
}

async function undoRoleHistory() {
  try {
    const response = await api('role_history/undo', historyRoleBody());
    applyHistory(response.history);
    byId('historyMessage').textContent = 'Restored the previous version. Press Undo again to switch back.';
  } catch (error) {
    byId('historyMessage').textContent = error.message || 'Nothing to undo.';
  }
}

// Called after a NEW role is saved with "start from" chosen in Search setup. The
// role exists already; its history is only a preview until saved here.
async function startHistoryFromRole(newPresetId, sourceKey) {
  historyRole = String(newPresetId);
  openWorkspace('assistantPanel', true, 'dashboard');
  await loadApplicationAssistant();
  await previewHistoryCopy(sourceKey, 'replace');
  const banner = byId('historyBanner');
  banner.textContent = `Your new role was saved. Below is the work history copied from ${historyRoleLabel(sourceKey)} for you to review. Press Save work history to keep it, or Discard unsaved changes to start clean. ` + banner.textContent;
  banner.hidden = false;
}

function fillStartFromOptions() {
  const select = byId('searchPresetStartFrom');
  select.replaceChildren(new Option('Start clean', ''),
    ...orderedSearchPresets().map(preset => new Option(`Start from ${preset.name}`, String(preset.id))));
}

byId('historyImport').onclick = importHistoryFromResume;
byId('historyCopy').onclick = showHistoryCopyPanel;
byId('historyCopyCancel').onclick = hideHistoryCopyPanel;
byId('historyCopyPreview').onclick = () => previewHistoryCopy(
  byId('historyCopySource').value, document.querySelector('input[name="historyCopyMode"]:checked').value);
byId('historyUndo').onclick = undoRoleHistory;
byId('saveRoleHistory').onclick = saveRoleHistory;
byId('historyDiscard').onclick = loadRoleHistory;
byId('historyAddJob').onclick = () => {
  historyState.work.push({id: '', title: '', employer: '', location: '', from: '', to: '', current: false, description: ''});
  dateEdited();
  renderHistory();
};
byId('historyAddEdu').onclick = () => {
  historyState.education.push({id: '', level: '', school: '', degree: '', field: '', location: '', graduated: ''});
  markHistoryDirty();
  renderHistory();
};
byId('historyVerified').onchange = () => {
  historyState.verified = byId('historyVerified').checked;
  markHistoryDirty();
  updateVerifiedNote();
};
