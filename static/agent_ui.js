// Apply for me, desktop session keys and Saved answers (backlog 19.2).
// Loaded after portal.js and shares its globals (API, byId, showNotice,
// showActionMessage, searchPresets, orderedSearchPresets, loadApplications).
// Every value that came from the server is written with textContent.

// The shared api() helper keeps only data.error, and the role picker needs
// data.code, so this reads the whole answer the same way api() does.
async function agentRequest(endpoint, method, body) {
  const response = await fetch(API + endpoint, {
    method,
    headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let data = {};
  try { data = await response.json(); } catch (error) { data = {}; }
  return {ok: response.ok, status: response.status, data};
}

// ── Apply for me ─────────────────────────────────────────────────────────────

function removeRolePicker(host) {
  const old = host.querySelector('.agent-role-picker');
  if (old) old.remove();
}

function showRolePicker(jobId, applyButton, host) {
  removeRolePicker(host);
  const picker = document.createElement('span');
  picker.className = 'agent-role-picker';
  const label = document.createElement('label');
  label.textContent = 'Apply under which role? ';
  const select = document.createElement('select');
  select.className = 'agent-role-select';
  select.add(new Option('Default', 'default'));
  orderedSearchPresets().forEach(preset => select.add(new Option(preset.name, 'preset:' + preset.id)));
  label.append(select);
  const go = document.createElement('button');
  go.type = 'button';
  go.className = 'primary';
  go.textContent = 'Queue';
  go.onclick = () => queueForAgent(jobId, select.value, applyButton, host);
  picker.append(label, go);
  host.append(picker);
}

async function queueForAgent(jobId, roleKey, button, host) {
  button.disabled = true;
  try {
    const body = {job_key: jobId};
    if (roleKey) body.role_key = roleKey;
    const result = await agentRequest('agent_queue', 'POST', body);
    if (result.ok) {
      removeRolePicker(host);
      showNotice(result.data.created
        ? 'Queued for your desktop agent. Nothing has been submitted.'
        : 'This job is already queued for your desktop agent.');
      if (typeof loadApplications === 'function') await loadApplications();
      return;
    }
    if (result.data.code === 'role_required') {
      showRolePicker(jobId, button, host);
      return;
    }
    showActionMessage(result.data.error || 'Could not queue this job.');
  } catch (error) {
    showActionMessage(error.message || 'Could not queue this job.');
  } finally {
    button.disabled = false;
  }
}

function createApplyForMeButton(jobId) {
  const host = document.createElement('span');
  host.className = 'agent-apply';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'primary apply-for-me';
  button.textContent = 'Apply for me';
  button.title = 'Queue this job for the Claude desktop agent. Nothing is submitted without your yes.';
  button.onclick = () => queueForAgent(jobId, null, button, host);
  host.append(button);
  return host;
}

// Opens Claude on this PC in the JobSearch folder with the apply skill. The URI
// is a constant on purpose: it carries no job id, path or key. The session pulls
// its work from the queue. It only works where the Project Portal bridge is
// installed; elsewhere the browser simply has no handler for it.
const APPLY_SESSION_URI = 'jppp://claude/jobsearch-apply';

function createApplySessionLink() {
  const link = document.createElement('a');
  link.className = 'secondary detail-link apply-session-link';
  link.href = APPLY_SESSION_URI;
  link.textContent = 'Open apply session';
  link.title = 'Opens the Claude apply session on the PC where the Project Portal bridge is installed. It takes no job; it works your apply queue.';
  return link;
}

// What an agent-owned attempt shows in place of the worker controls.
function agentAttemptSummary(attempt) {
  const box = document.createElement('div');
  const note = document.createElement('p');
  note.className = 'muted';
  note.textContent = attempt.state === 'needs_user_input'
    ? 'The desktop agent is waiting for your answers. Answer them under Saved answers and the job goes back in the queue.'
    : 'This application is handled by your Claude desktop agent. Nothing is submitted without your yes in that session.';
  box.append(note);
  const review = attempt.review;
  if (review && review.summary) {
    const title = document.createElement('h4');
    title.textContent = 'Review recorded by the agent';
    const summary = document.createElement('p');
    summary.textContent = review.summary;
    box.append(title, summary);
    if (Array.isArray(review.filled_fields) && review.filled_fields.length) {
      const list = document.createElement('ul');
      review.filled_fields.forEach(field => {
        const item = document.createElement('li');
        item.textContent = `${field.label}: ${field.value}`;
        list.append(item);
      });
      box.append(list);
    }
  }
  return box;
}

// ── Desktop session key ──────────────────────────────────────────────────────

function utcText(value) {
  return value ? new Date(String(value) + 'Z').toLocaleString() : 'never';
}

// Each environment has its own key and its own file, so a DEV key and a PROD key never share one.
const APP_ENV = /_dev$/.test(PREFIX) ? 'dev' : 'prod';
const DESKTOP_KEY_FILE = '%LOCALAPPDATA%\\jobsearch\\desktop_key.' + APP_ENV;

function clearDesktopKeyReveal() {
  const reveal = byId('desktopKeyNew');
  if (reveal) { reveal.replaceChildren(); reveal.hidden = true; }
}

async function loadDesktopKeys() {
  const result = await agentRequest('desktop_session_keys', 'GET');
  if (!result.ok) throw new Error(result.data.error || 'Could not load desktop keys');
  byId('desktopKeyStatus').textContent = result.data.agent_api_enabled
    ? ''
    : 'The desktop agent API is turned off on this server, so a key cannot be used yet.';
  const list = byId('desktopKeyList');
  list.replaceChildren();
  (result.data.keys || []).forEach(key => {
    const row = document.createElement('div');
    row.className = 'assistant-key-row';
    const text = document.createElement('span');
    text.textContent = `${key.label} · created ${utcText(key.created_at)} · last used ${utcText(key.last_used_at)} · expires ${utcText(key.expires_at)}`;
    const revoke = document.createElement('button');
    revoke.type = 'button';
    revoke.className = 'secondary';
    revoke.textContent = 'Revoke';
    revoke.onclick = async () => {
      if (!confirm('Revoke this desktop key? A session using it stops working immediately.')) return;
      const response = await agentRequest('desktop_session_keys/' + encodeURIComponent(key.key_id), 'DELETE');
      byId('desktopKeyMessage').textContent = response.ok ? '' : (response.data.error || 'Could not revoke the key.');
      await loadDesktopKeys();
    };
    row.append(text, revoke);
    list.append(row);
  });
  if (!(result.data.keys || []).length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No desktop keys yet.';
    list.append(empty);
  }
}

async function createDesktopKey() {
  const button = byId('createDesktopKey'), message = byId('desktopKeyMessage');
  button.disabled = true;
  message.textContent = '';
  try {
    const result = await agentRequest('desktop_session_keys', 'POST', {label: byId('desktopKeyLabel').value});
    if (!result.ok) {
      message.textContent = result.data.error || 'Could not create the key.';
      return;
    }
    const reveal = byId('desktopKeyNew');
    reveal.replaceChildren();
    const warning = document.createElement('p');
    warning.textContent = 'Copy this key now. It is shown only once and cannot be shown again.';
    const token = document.createElement('code');
    token.className = 'desktop-key-token';
    token.textContent = result.data.token;
    const where = document.createElement('p');
    where.className = 'muted';
    where.textContent = 'Save it as one line in ' + DESKTOP_KEY_FILE + '. The apply skill reads it from that file and keeps the other environment\'s key separate. This page cannot write the file for you.';
    const done = document.createElement('button');
    done.type = 'button';
    done.className = 'secondary';
    done.textContent = 'I saved it';
    done.onclick = clearDesktopKeyReveal;
    reveal.append(warning, token, where, done);
    reveal.hidden = false;
    byId('desktopKeyLabel').value = '';
    await loadDesktopKeys();
  } catch (error) {
    message.textContent = error.message || 'Could not create the key.';
  } finally {
    button.disabled = false;
  }
}

// ── Saved answers ────────────────────────────────────────────────────────────

const SAVED_ANSWER_SOURCES = {agent_chat: 'told to the agent in chat', user_ui: 'saved here by you'};

function renderSavedAnswer(item) {
  const row = document.createElement('div');
  row.className = 'saved-answer';
  const question = document.createElement('strong');
  question.textContent = item.question;
  const meta = document.createElement('span');
  meta.className = 'muted';
  meta.textContent = item.status === 'needs_answer'
    ? ' · waiting for your answer'
    : ` · ${SAVED_ANSWER_SOURCES[item.source] || item.source} · seen ${item.times_seen} time${item.times_seen === 1 ? '' : 's'}`;
  const input = document.createElement('textarea');
  input.className = 'saved-answer-input';
  input.maxLength = 2000;
  input.value = item.answer || '';
  const reuseLabel = document.createElement('label');
  reuseLabel.className = 'assistant-check';
  const reuse = document.createElement('input');
  reuse.type = 'checkbox';
  reuse.className = 'saved-answer-reuse';
  reuse.checked = item.safe_to_autofill;
  reuse.disabled = item.status !== 'confirmed';
  reuseLabel.append(reuse, document.createTextNode(' Reuse this answer'));
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'primary';
  save.textContent = 'Save';
  save.onclick = async () => {
    const body = {answer: input.value};
    if (item.status === 'confirmed') body.safe_to_autofill = reuse.checked;
    const result = await agentRequest('question_bank/' + encodeURIComponent(item.id), 'POST', body);
    byId('savedAnswersMessage').textContent = result.ok ? '' : (result.data.error || 'Could not save the answer.');
    await loadSavedAnswers();
  };
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'danger';
  remove.textContent = 'Delete';
  remove.onclick = async () => {
    if (!confirm('Delete this saved answer?')) return;
    const result = await agentRequest('question_bank/' + encodeURIComponent(item.id), 'DELETE');
    byId('savedAnswersMessage').textContent = result.ok ? '' : (result.data.error || 'Could not delete the answer.');
    await loadSavedAnswers();
  };
  const actions = document.createElement('div');
  actions.className = 'actions';
  actions.append(save, remove);
  row.append(question, meta, input, reuseLabel, actions);
  return row;
}

async function loadSavedAnswers() {
  const result = await agentRequest('question_bank', 'GET');
  if (!result.ok) throw new Error(result.data.error || 'Could not load saved answers');
  const list = byId('savedAnswersList');
  list.replaceChildren();
  (result.data.answers || []).forEach(item => list.append(renderSavedAnswer(item)));
  if (!(result.data.answers || []).length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Nothing saved yet. Questions the desktop agent asks you will appear here.';
    list.append(empty);
  }
}

async function loadAgentCards() {
  try { await Promise.all([loadDesktopKeys(), loadSavedAnswers()]); }
  catch (error) { showActionMessage(error.message || 'Could not load the desktop agent cards.'); }
}

(function wireAgentCards() {
  const create = byId('createDesktopKey');
  if (create) create.onclick = createDesktopKey;
  document.querySelectorAll('.desktop-key-file').forEach(node => { node.textContent = DESKTOP_KEY_FILE; });
  const panel = byId('assistantPanel');
  if (panel && typeof MutationObserver === 'function') {
    // Leaving the panel removes a freshly created key from the page.
    new MutationObserver(() => { if (panel.hidden) clearDesktopKeyReveal(); })
      .observe(panel, {attributes: true, attributeFilter: ['hidden']});
  }
})();
