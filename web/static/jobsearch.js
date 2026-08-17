/* jobsearch.js — shared static JS for Job Search Agent
 * Globals expected to be set inline by each page before this script loads:
 *   API_BASE, API_ENV, userName, isTestMode, JOB_STATUS_STORAGE_KEY, configData
 */

/* -------- Sorting -------- */
function sortTable(n, forceDesc) {
  forceDesc = forceDesc || false;
  var table = document.getElementById("jobTable");
  var rows = Array.from(table.rows).slice(1);
  var asc = table.getAttribute("data-sort-dir") !== "asc";
  if (forceDesc) asc = false;
  rows.sort(function(a, b) {
    var x = a.cells[n].innerText.toLowerCase();
    var y = b.cells[n].innerText.toLowerCase();
    return asc ? x.localeCompare(y, undefined, {numeric: true})
               : y.localeCompare(x, undefined, {numeric: true});
  });
  rows.forEach(function(r) { table.appendChild(r); });
  table.setAttribute("data-sort-dir", asc ? "asc" : "desc");
}
window.onload = function() {
  var table = document.getElementById('jobTable');
  if (table && table.rows.length > 1) sortTable(6, true);
};

/* -------- Modal open/close/drag/resize with persistence -------- */
var MODAL_POS_KEY  = "editRunModalPos";
var MODAL_SIZE_KEY = "editRunModalSize";

async function openEditRunModal() {
  var modal = document.getElementById('editModal');

  /* restore size */
  var savedSize = localStorage.getItem(MODAL_SIZE_KEY);
  if (savedSize) {
    try {
      var sz = JSON.parse(savedSize);
      if (sz.width)  modal.style.width  = sz.width + "px";
      if (sz.height) modal.style.height = sz.height + "px";
    } catch (e) {}
  }

  /* restore position */
  var savedPos = localStorage.getItem(MODAL_POS_KEY);
  if (savedPos) {
    try {
      var pos = JSON.parse(savedPos);
      modal.style.left = pos.left + "px";
      modal.style.top  = pos.top  + "px";
      modal.classList.remove('centered');
      modal.style.transform = "";
    } catch (e) {}
  } else {
    modal.classList.add('centered');
  }

  modal.style.display = 'flex';
  makeDraggable(modal);
  attachResizeHandles(modal);

  /* Inject shared fixed-position tooltip div (once per page load) */
  if (!document.getElementById('js-tooltip')) {
    var tipEl = document.createElement('div');
    tipEl.id = 'js-tooltip';
    tipEl.className = 'js-tooltip';
    document.body.appendChild(tipEl);
  }

  /* Fetch live config from API instead of using stale embedded data */
  try {
    var resp = await fetch(apiUrl('load_config', { user: userName, test_mode: isTestMode }));
    if (resp.ok) {
      var data = await resp.json();
      if (data.success && data.config) {
        Object.keys(configData).forEach(function(k) { delete configData[k]; });
        Object.assign(configData, data.config);
      }
    }
  } catch (e) {
    console.warn('Could not fetch live config, using embedded defaults:', e);
  }

  renderConfigEditor();
  calculateSerpApiCost();
  fetchSerpApiRemaining();
}

function closeEditRunModal() {
  if (jobRunning) {
    if (!confirm('A job search is running. Close anyway? (Use Cancel to stop it first)')) return;
    stopPolling();
    jobRunning = false;
  }
  stopPolling();
  var tip = document.getElementById('js-tooltip');
  if (tip) tip.style.display = 'none';
  document.getElementById('editModal').style.display = 'none';
}

function cancelJobSearch() {
  if (!jobRunning) {
    hideProgressSection();
    return;
  }

  var cancelBtn = document.getElementById('cancelJobBtn');
  cancelBtn.disabled = true;
  cancelBtn.textContent = 'Cancelling...';

  fetch(apiUrl('cancel'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user: userName, test_mode: isTestMode })
  })
  .then(function(response) { return response.json(); })
  .then(function(data) {
    stopPolling();
    jobRunning = false;
    lastDisplayedTimestamp = '';
    var startBtn = document.getElementById('startJobBtn');
    startBtn.disabled = false;
    startBtn.textContent = '\u25B6 Start Job Search';
    cancelBtn.disabled = false;
    cancelBtn.textContent = '\u25C0 Back to Config';
    appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2716 Job cancelled.');
    setTimeout(function() { hideProgressSection(); }, 800);
  })
  .catch(function(err) {
    stopPolling();
    jobRunning = false;
    cancelBtn.disabled = false;
    cancelBtn.textContent = '\u25C0 Back to Config';
    appendJobOutput('[' + new Date().toLocaleTimeString() + '] Cancel request failed: ' + err.message);
    setTimeout(function() { hideProgressSection(); }, 800);
  });
}

/* Drag via header — independent of inner scroll */
function makeDraggable(modal) {
  if (modal._draggableAttached) return;  // guard: only wire once per modal instance
  modal._draggableAttached = true;
  var header = modal.querySelector('.modal-header');
  var pos = {dragging: false, startX: 0, startY: 0, left: 0, top: 0};
  header.onmousedown = function(e) {
    e.preventDefault();
    pos.dragging = true;
    modal.classList.remove('centered');
    modal.style.transform = "";
    pos.startX = e.clientX;
    pos.startY = e.clientY;
    pos.left = modal.offsetLeft;
    pos.top  = modal.offsetTop;
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onDrag);
    document.addEventListener('mouseup', onStop);
    window.addEventListener('mouseup', onStop);  // catch release outside browser window
  };
  function onDrag(e) {
    if (!pos.dragging) return;
    modal.style.left = (pos.left + e.clientX - pos.startX) + "px";
    modal.style.top  = (pos.top  + e.clientY - pos.startY) + "px";
  }
  function onStop() {
    pos.dragging = false;
    document.body.style.userSelect = '';
    document.removeEventListener('mousemove', onDrag);
    document.removeEventListener('mouseup', onStop);
    window.removeEventListener('mouseup', onStop);
    localStorage.setItem(MODAL_POS_KEY, JSON.stringify({ left: modal.offsetLeft, top: modal.offsetTop }));
  }
}

/* Multi-edge resize handles (all edges + corners) */
function attachResizeHandles(modal) {
  if (modal._handlesAttached) return;
  var dirs = ['n','s','e','w','ne','nw','se','sw'];
  dirs.forEach(function(d) {
    var h = document.createElement('div');
    h.className = 'resize-handle resize-' + d;
    modal.appendChild(h);
    h.addEventListener('mousedown', function(e) { startResize(e, modal, d); });
  });
  modal._handlesAttached = true;
}

function startResize(e, modal, dir) {
  e.preventDefault();
  var startX = e.clientX, startY = e.clientY;
  var startW = modal.offsetWidth, startH = modal.offsetHeight;
  var startL = modal.offsetLeft, startT = modal.offsetTop;

  function onMove(ev) {
    var dx = ev.clientX - startX;
    var dy = ev.clientY - startY;
    var newW = startW, newH = startH, newL = startL, newT = startT;

    if (dir.includes('e')) newW = Math.max(480, startW + dx);
    if (dir.includes('s')) newH = Math.max(320, startH + dy);
    if (dir.includes('w')) { newW = Math.max(480, startW - dx); newL = startL + (startW - newW); }
    if (dir.includes('n')) { newH = Math.max(320, startH - dy); newT = startT + (startH - newH); }

    modal.style.width  = newW + 'px';
    modal.style.height = newH + 'px';
    modal.style.left   = newL + 'px';
    modal.style.top    = newT + 'px';
    modal.classList.remove('centered');
    modal.style.transform = "";
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    document.body.style.userSelect = '';
    localStorage.setItem(MODAL_SIZE_KEY, JSON.stringify({ width: modal.offsetWidth, height: modal.offsetHeight }));
    localStorage.setItem(MODAL_POS_KEY,  JSON.stringify({ left: modal.offsetLeft, top: modal.offsetTop }));
  }

  document.body.style.userSelect = 'none';
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

/* -------- Tooltips -------- */
var tooltips = {
  "serpapi_key": "Your SerpAPI API key. Required when sources.serpapi is enabled.\n\nGet your key at serpapi.com. Each search page consumes one credit from your monthly plan. The SerpAPI Requests Remaining counter above shows your current balance.",
  "adzuna_credentials": "API credentials for the Adzuna job board.\n\napp_id and app_key are issued separately at developer.adzuna.com. Both are required for Adzuna searches. Adzuna is free tier friendly and rarely blocked.",
  "sources": "Toggle each job source on or off for this search.\n\nadzuna — Reliable, free API with broad coverage. Recommended.\nserpapi — Google Jobs scraper. Uses monthly credits; good for remote/national searches.\nindeed — Frequently returns 401/403 errors and is usually blocked. Disable unless testing.",
  "keywords": "Job title or skill phrases to search for. Enter as comma-separated values — no quotes needed.\n\nExample: software engineer, python developer, data engineer\n\nA job must match at least one keyword (OR logic) to pass filtering. Keywords are checked against the job title, snippet, and full description. Case-insensitive.\n\nTip: broader terms (engineer, developer) yield more results; narrow phrases (senior python engineer) are stricter.",
  "blocked_words": "Jobs where the job title contains any of these words are excluded entirely. Only the title is checked — not the description.\n\nExample: intern, junior, director, manager, sales\n\nUse this to filter out role levels that consistently appear in results but are not relevant.",
  "location": "The geographic region sent to job scrapers as a search parameter during the local search pass.\n\nFormat: City, State  or  State name  (e.g. Allentown, PA or Pennsylvania)\n\nThis field only affects what the scrapers search for — it does NOT control distance filtering. Distance filtering is controlled separately by zip_code + radius_miles.\n\nWhen search_remote is enabled, the remote pass ignores this field entirely and searches nationally.\n\nFor best results, location and zip_code should point to the same general area.",
  "remote": "Legacy field — keep this consistent with search_remote.\n\nWhen true, scrapers omit the location parameter and search nationally. Prefer using search_remote and search_local instead of this field for clearer control.",
  "search_remote": "When enabled, runs a nationwide search pass with no location filter.\n\nRemote jobs found this way bypass distance filtering entirely — they are included regardless of zip_code or radius_miles. Combine with search_local to get both remote and nearby results in a single run.",
  "search_local": "When enabled, runs a location-based search pass near your configured location field.\n\nLocal jobs are then distance-filtered by zip_code and radius_miles. Jobs without coordinates are filtered by state (non-PA jobs are dropped). Combine with search_remote for both local and remote results.",
  "adzuna_local_max_pages": "Number of pages to fetch from Adzuna for the LOCAL search pass (near your location).\n\nLocal results cluster on the first 1-3 pages — additional pages rarely add useful nearby jobs. Recommended: 2-3.\n\nEach page = adzuna_results_per_page jobs.",
  "serpapi_local_max_pages": "Number of pages to fetch from SerpAPI for the LOCAL search pass.\n\nEach page = 1 credit, returns up to 10 results (Google Jobs hard limit — not configurable).\n\nCost: this value × number of keywords = credits per local pass.\n\nKeep this low (1-2) — Adzuna provides better regional coverage than SerpAPI for local searches.",
  "indeed_local_max_pages": "Pages to fetch from Indeed for the LOCAL pass. Indeed is frequently blocked (401/403 errors) and may return no results regardless of this setting. Disable the Indeed source unless specifically testing.",
  "zip_code": "The ZIP code used as the center point for distance filtering.\n\nWorks with any valid US ZIP code — coordinates are looked up automatically the first time a ZIP is used and cached for subsequent runs.\n\nThis field only affects filtering of job results. It has no effect on what the scrapers search for (that is controlled by the location field).",
  "radius_miles": "Maximum distance in miles from zip_code for non-remote jobs.\n\nHow distance filtering works:\n\n1. Job has coordinates (lat/lon from scraper):\n   Haversine great-circle distance is calculated from the zip_code center point. Pass if ≤ radius_miles, fail if outside. This works for any location worldwide.\n\n2. Job has no coordinates:\n   ⚠️ Falls back to a hardcoded PA-only rule — scans the job's location string for US state abbreviations and rejects anything that is not PA. This fallback ignores both zip_code and radius_miles entirely.\n\n   Known limitation: if you configure location as 'New Jersey' or any non-PA location, jobs without coordinates that come back from that search will still be rejected by this fallback even if they are nearby. Only jobs that include lat/lon bypass this restriction.\n\nRemote jobs skip all distance filtering and always pass.",
  "salary_min": "Minimum annual salary in dollars. Jobs that report a salary below this are filtered out.\n\nJobs with no salary data are NOT filtered — salary information is sparse across all sources, so filtering on it is lenient by design. Set to 0 to disable salary filtering entirely.",
  "scoring_method": "How jobs are ranked against your resume after filtering.\n\ntfidf — Fast keyword overlap. Good for quick test runs. Exact word matching only.\n\nsemantic — Meaning-aware scoring using the all-MiniLM-L6-v2 sentence transformer model. Understands that 'Python engineer' and 'software developer' are related. Slower but significantly smarter. Recommended.\n\nhybrid — Weighted combination (60% semantic + 40% tfidf). Best overall quality for most use cases.",
  "boost_terms": "Additional keywords that get bonus score weight if they appear in a job's title or description. Comma-separated.\n\nExample: python, aws, remote, kubernetes\n\nBoost terms do NOT filter jobs out — they only increase the score of matching jobs, pushing them higher in results. Use for skills or preferences that are important but not required.",
  "boost_weight": "How much score each boost_term match adds. Added per matched term, capped at 1.0 total.\n\nExample: boost_weight 0.15 with 2 matching boost_terms adds 0.30 to that job's score.\n\nTypical useful range: 0.05 – 0.25. Higher values make boost terms dominate the ranking.",
  "match_threshold": "Minimum similarity score (0.0 – 1.0) a job must reach to appear in results.\n\nLower = more results, less precise. Higher = fewer results, better matches.\n\nTypical useful range:\n  0.10 – 0.18 → broad results, lots of jobs\n  0.18 – 0.25 → balanced (recommended starting point)\n  0.25 – 0.40 → strict, high-confidence matches only",
  "serpapi_max_pages": "Number of pages per keyword fetched from SerpAPI for the REMOTE/national search pass.\n\nEach page = 1 credit, returns up to 10 results (Google Jobs hard limit — not configurable).\n\nTotal credits = serpapi_max_pages × keywords\nMax results = serpapi_max_pages × keywords × 10\n\nKeep this low (3-5) to preserve your monthly credit balance.",
  "serpapi_results_per_page": "⚠️ This setting has no effect on SerpAPI Google Jobs searches.\n\nGoogle Jobs returns a maximum of 10 results per page regardless of this value — the num parameter is not supported by the Google Jobs engine. This field is kept for reference only and is ignored by the scraper.",
  "adzuna_max_pages": "Number of pages per keyword fetched from Adzuna for the REMOTE/national search pass.\n\nAdzuna is free and not credit-limited, so higher values are fine. However, result quality drops significantly past page 5-10. Recommended: 3-7.",
  "adzuna_results_per_page": "Number of job results per Adzuna page. Max is 50.\n\nAdzuna paginates independently per keyword. Total fetched = adzuna_max_pages × adzuna_results_per_page × number of keywords.",
  "indeed_max_pages": "Pages to fetch from Indeed per keyword. Indeed is frequently blocked and may return errors regardless of this value. Usually safe to leave at default.",
  "indeed_results_per_page": "Results per Indeed page. Indeed pagination is unreliable when the source is blocked."
};

/* -------- Tooltip event handlers (position:fixed, escapes modal overflow) -------- */
function _tipOver(e) {
  var icon = e.target.closest ? e.target.closest('.info-icon') : null;
  if (!icon) return;
  var tipText = icon.getAttribute('data-tip');
  if (!tipText) return;
  var tooltip = document.getElementById('js-tooltip');
  if (!tooltip) return;
  tooltip.textContent = tipText;
  tooltip.style.display = 'block';
  var r = icon.getBoundingClientRect();
  var left = r.left;
  if (left + 420 > window.innerWidth - 8) left = window.innerWidth - 428;
  if (left < 8) left = 8;
  tooltip.style.left = left + 'px';
  tooltip.style.top = (r.bottom + 6) + 'px';
}
function _tipOut(e) {
  var icon = e.target.closest ? e.target.closest('.info-icon') : null;
  if (!icon) return;
  if (icon.contains(e.relatedTarget)) return;
  var tooltip = document.getElementById('js-tooltip');
  if (tooltip) tooltip.style.display = 'none';
}

/* -------- Config editor layout metadata -------- */

var FIELD_META = {
  // Hidden — no DOM element; stripped from JSON files
  '____comment_scoring_method': { hidden: true },
  '____comment_keywords':       { label: 'Keyword Pool (staging)' },
  '____comment_serpapikey':     { hidden: true },
  'remote':                     { hidden: true },

  // Credential keys — all hidden, rendered by custom credentials section
  'credentials':        { hidden: true },
  'serpapi_key':        { hidden: true },  // legacy — migrated to credentials
  '____my_serpapikey':  { hidden: true },  // legacy — migrated to credentials
  '____dad_serpapikey': { hidden: true },  // legacy — migrated to credentials
  'adzuna_credentials': { hidden: true },  // legacy — migrated to credentials

  // Label overrides
  'zip_code':       { label: 'ZIP Code' },
  'radius_miles':   { label: 'Radius (miles)' },
  'search_remote':  { label: 'Search Remote (national)' },
  'search_local':   { label: 'Search Local (near location)' },
  'salary_min':     { label: 'Minimum Salary ($)' },
  'match_threshold':{ label: 'Match Threshold' },
  'boost_terms':    { label: 'Boost terms' },
  'boost_weight':   { label: 'Weight' },

  // Widget overrides
  'scoring_method': { widget: 'select', options: ['semantic', 'tfidf', 'hybrid'], label: 'Scoring Method' }
};

var SOURCE_PAGINATION = {
  adzuna:  ['adzuna_max_pages',  'adzuna_local_max_pages',  'adzuna_results_per_page'],
  serpapi: ['serpapi_max_pages', 'serpapi_local_max_pages', 'serpapi_results_per_page'],
  indeed:  ['indeed_max_pages',  'indeed_local_max_pages',  'indeed_results_per_page']
};
var SOURCE_PAGINATION_DEFAULTS = {
  adzuna_max_pages: 5, adzuna_local_max_pages: 3, adzuna_results_per_page: 30,
  serpapi_max_pages: 5, serpapi_local_max_pages: 2, serpapi_results_per_page: 10,
  indeed_max_pages: 5, indeed_local_max_pages: 2, indeed_results_per_page: 30
};
var SOURCE_PAGINATION_ALL = [
  'adzuna_max_pages', 'adzuna_local_max_pages', 'adzuna_results_per_page',
  'serpapi_max_pages', 'serpapi_local_max_pages', 'serpapi_results_per_page',
  'indeed_max_pages', 'indeed_local_max_pages', 'indeed_results_per_page'
];

var GROUPS = [
  { title: 'Search Terms',        keys: ['keywords', '____comment_keywords', 'blocked_words'] },
  { title: 'Scraper Credentials', type: 'credentials' },
  { title: 'Sources',             type: 'sources' },
  { title: 'Location',            keys: ['location', 'zip_code', 'radius_miles'] },
  { title: 'Search Passes',       keys: ['search_remote', 'search_local'] },
  { title: 'Scoring',             type: 'scoring' },
  { title: 'Salary',              keys: ['salary_min'] }
];

var CREDENTIAL_SCHEMA = {
  serpapi: { fields: ['key'], labels: { key: 'API Key' }, inlineLabels: { key: 'Key:' } },
  adzuna: { fields: ['app_id', 'app_key'], labels: { app_id: 'App ID', app_key: 'App Key' }, inlineLabels: { app_id: 'ID:', app_key: 'Key:' } }
};

/* -------- Config rendering -------- */
function renderField(key, value, path, meta) {
  meta = meta || FIELD_META[key] || {};

  // Hidden — no DOM element
  if (meta.hidden) return '';

  var id = 'cfg__' + path.join('__');
  var displayLabel = meta.label || key.replace(/^_{2,}/, '');
  var tip = tooltips[key] || '';
  var tipHTML = tip ? '<span class="info-icon" data-tip="' + tip.replace(/"/g, '&quot;').replace(/\n/g, '&#10;') + '">\uD83D\uDD0D</span>' : '';

  // SELECT widget (e.g. scoring_method dropdown)
  if (meta.widget === 'select') {
    var opts = (meta.options || []).map(function(o) {
      return '<option value="' + o + '"' + (value === o ? ' selected' : '') + '>' + o + '</option>';
    }).join('');
    return '\n<div class="row">'
      + '<label for="' + id + '">' + displayLabel + tipHTML + '</label>'
      + '<div class="field-col"><select id="' + id + '">' + opts + '</select></div>'
      + '</div>';
  }

  // DISABLED NUMBER (serpapi_results_per_page — no effect on Google Jobs)
  if (key === 'serpapi_results_per_page' || meta.widget === 'disabled-number') {
    return '\n<div class="row" style="opacity:0.45;">'
      + '<label for="' + id + '" style="text-decoration:line-through;">' + displayLabel + tipHTML + '</label>'
      + '<div class="field-col"><input id="' + id + '" type="number" step="any" value="' + value + '" disabled data-always-disabled="true" style="cursor:not-allowed;"></div>'
      + '</div>';
  }

  var rowClass = 'row';

  if (typeof value === 'boolean') {
    return '\n<div class="' + rowClass + '">\n  <label for="' + id + '">' + displayLabel + tipHTML + '</label>\n  <div class="field-col"><input id="' + id + '" type="checkbox" ' + (value ? 'checked' : '') + '></div>\n</div>';
  } else if (typeof value === 'number') {
    return '\n<div class="' + rowClass + '">\n  <label for="' + id + '">' + displayLabel + tipHTML + '</label>\n  <div class="field-col"><input id="' + id + '" type="number" step="any" value="' + value + '"></div>\n</div>';
  } else if (Array.isArray(value)) {
    return '\n<div class="' + rowClass + '">\n  <label for="' + id + '">' + displayLabel + tipHTML + '</label>\n  <div class="field-col"><textarea id="' + id + '" rows="2">' + value.join(', ') + '</textarea></div>\n</div>';
  } else if (value && typeof value === 'object') {
    var inner = Object.keys(value).map(function(k) { return renderField(k, value[k], path.concat([k])); }).join('');
    return '\n<div class="section">\n  <div class="section-title">' + displayLabel + '</div>\n  ' + inner + '\n</div>';
  } else {
    return '\n<div class="' + rowClass + '">\n  <label for="' + id + '">' + displayLabel + tipHTML + '</label>\n  <div class="field-col"><input id="' + id + '" type="text" value="' + (value != null ? value : '') + '"></div>\n</div>';
  }
}

/* -------- Credentials section renderer -------- */
function renderCredentialsSection() {
  var creds = configData.credentials || {};
  var html = '';
  ['serpapi', 'adzuna'].forEach(function(scraper) {
    var schema = CREDENTIAL_SCHEMA[scraper];
    var entries = creds[scraper] || [];
    var displayName = scraper === 'serpapi' ? 'SerpAPI' : scraper.charAt(0).toUpperCase() + scraper.slice(1);
    html += '<div class="credential-scraper-header">'
      + displayName
      + '<span class="cred-add" title="Add" onclick="addCredential(\'' + scraper + '\')">+</span>'
      + '</div>';
    entries.forEach(function(entry, idx) {
      html += '<div class="credential-row" data-scraper="' + scraper + '" data-index="' + idx + '">';
      html += '<input type="text" class="cred-name" value="' + (entry.name || '') + '" placeholder="Name">';
      html += '<span class="cred-delete" title="Delete" onclick="deleteCredential(\'' + scraper + '\', ' + idx + ')">-</span>';
      schema.fields.forEach(function(f) {
        html += '<label class="cred-label">' + (schema.inlineLabels[f] || f) + '</label>';
        html += '<input type="text" class="cred-field" data-field="' + f + '" value="' + (entry[f] || '') + '" placeholder="' + (schema.labels[f] || f) + '">';
      });
      html += '</div>';
    });
  });
  return html;
}

function addCredential(scraper) {
  var schema = CREDENTIAL_SCHEMA[scraper];
  var name = prompt('Credential name:');
  if (name === null || !name.trim()) return;
  var entry = { name: name.trim() };
  for (var i = 0; i < schema.fields.length; i++) {
    var f = schema.fields[i];
    var val = prompt(schema.labels[f] + ':');
    if (val === null) return;
    entry[f] = val;
  }
  if (!configData.credentials) configData.credentials = {};
  if (!configData.credentials[scraper]) configData.credentials[scraper] = [];
  configData.credentials[scraper].push(entry);
  renderConfigEditor();
  calculateSerpApiCost();
}

function deleteCredential(scraper, index) {
  if (!configData.credentials || !configData.credentials[scraper]) return;
  // Read name from live DOM, not stale configData (user may have edited name without saving)
  var row = document.querySelector(
    '.credential-row[data-scraper="' + scraper + '"][data-index="' + index + '"]'
  );
  var liveName = row ? row.querySelector('.cred-name').value.trim() : '';
  if (liveName.toLowerCase() === 'active') {
    alert('Cannot delete the Active credential. Rename it first or copy a different key into it.');
    return;
  }
  configData.credentials[scraper].splice(index, 1);
  renderConfigEditor();
  calculateSerpApiCost();
}

function collectCredentials() {
  var creds = {};
  Object.keys(CREDENTIAL_SCHEMA).forEach(function(scraper) {
    creds[scraper] = [];
    var rows = document.querySelectorAll('.credential-row[data-scraper="' + scraper + '"]');
    rows.forEach(function(row) {
      var entry = { name: row.querySelector('.cred-name').value };
      CREDENTIAL_SCHEMA[scraper].fields.forEach(function(f) {
        entry[f] = row.querySelector('.cred-field[data-field="' + f + '"]').value;
      });
      creds[scraper].push(entry);
    });
  });
  return creds;
}

/* -------- Source pagination toggle (enable/disable on checkbox change) -------- */
function toggleSourcePagination(src, enabled) {
  var group = document.getElementById('src-pg-' + src);
  if (!group) return;
  var inputs = group.querySelectorAll('input:not([data-always-disabled]), textarea, select');
  inputs.forEach(function(el) { el.disabled = !enabled; });
  group.style.opacity = enabled ? '1' : '0.45';
}

/* -------- Sources section renderer -------- */
function renderSourcesSection() {
  var html = '<div class="section">';
  html += '<div class="section-title">Sources</div>';
  var sources = configData.sources || {};
  ['adzuna', 'serpapi', 'indeed'].forEach(function(src) {
    var checked = sources[src] ? 'checked' : '';
    var srcId = 'cfg__sources__' + src;
    var tip = tooltips['sources'] || '';
    var tipHTML = (src === 'adzuna' && tip)
      ? '<span class="info-icon" data-tip="' + tip.replace(/"/g, '&quot;').replace(/\n/g, '&#10;') + '">\uD83D\uDD0D</span>' : '';
    html += '\n<div class="row">'
      + '<label for="' + srcId + '">' + src + tipHTML + '</label>'
      + '<div class="field-col"><input id="' + srcId + '" type="checkbox" ' + checked
      + ' onchange="toggleSourcePagination(\'' + src + '\', this.checked)"></div>'
      + '</div>';
    var pgEnabled = !!sources[src];
    html += '<div class="source-pagination-group" id="src-pg-' + src + '"'
      + (pgEnabled ? '' : ' style="opacity:0.45;"') + '>';
    SOURCE_PAGINATION[src].forEach(function(pkey) {
      var pval = configData[pkey];
      if (pval === undefined) pval = SOURCE_PAGINATION_DEFAULTS[pkey];
      var pgHtml = renderField(pkey, pval, [pkey]);
      if (!pgEnabled) {
        // Mark inputs disabled on initial render for unchecked sources
        pgHtml = pgHtml.replace(/<input /g, '<input disabled ');
        pgHtml = pgHtml.replace(/<select /g, '<select disabled ');
        pgHtml = pgHtml.replace(/<textarea /g, '<textarea disabled ');
      }
      html += pgHtml;
    });
    html += '</div>';
  });
  html += '</div>';
  return html;
}

/* -------- Scoring section renderer -------- */
function renderScoringSection() {
  var html = '';

  // Scoring Method + Match Threshold inline pair
  var smVal = configData.scoring_method || 'semantic';
  var mtVal = configData.match_threshold !== undefined ? configData.match_threshold : '';
  var smTip = tooltips['scoring_method'] || '';
  var mtTip = tooltips['match_threshold'] || '';
  var smTipHTML = smTip ? '<span class="info-icon" data-tip="' + smTip.replace(/"/g, '&quot;').replace(/\n/g, '&#10;') + '">\uD83D\uDD0D</span>' : '';
  var mtTipHTML = mtTip ? '<span class="info-icon" data-tip="' + mtTip.replace(/"/g, '&quot;').replace(/\n/g, '&#10;') + '">\uD83D\uDD0D</span>' : '';
  var smOpts = ['semantic', 'tfidf', 'hybrid'].map(function(o) {
    return '<option value="' + o + '"' + (smVal === o ? ' selected' : '') + '>' + o + '</option>';
  }).join('');
  html += '\n<div class="row scoring-pair-row">'
    + '<div class="scoring-pair-fields">'
    +   '<div class="scoring-method-col">'
    +     '<label for="cfg__scoring_method">Scoring Method' + smTipHTML + '</label>'
    +     '<select id="cfg__scoring_method">' + smOpts + '</select>'
    +   '</div>'
    +   '<div class="match-threshold-col">'
    +     '<label for="cfg__match_threshold">Match Threshold' + mtTipHTML + '</label>'
    +     '<input id="cfg__match_threshold" type="number" step="any" value="' + mtVal + '">'
    +   '</div>'
    + '</div>'
    + '</div>';

  // Boost terms + weight inline pair
  var btVal = Array.isArray(configData.boost_terms) ? configData.boost_terms.join(', ') : (configData.boost_terms || '');
  var bwVal = configData.boost_weight !== undefined ? configData.boost_weight : '';
  var btTip = tooltips['boost_terms'] || '';
  var bwTip = tooltips['boost_weight'] || '';
  var btTipHTML = btTip ? '<span class="info-icon" data-tip="' + btTip.replace(/"/g, '&quot;').replace(/\n/g, '&#10;') + '">\uD83D\uDD0D</span>' : '';
  var bwTipHTML = bwTip ? '<span class="info-icon" data-tip="' + bwTip.replace(/"/g, '&quot;').replace(/\n/g, '&#10;') + '">\uD83D\uDD0D</span>' : '';
  html += '\n<div class="row boost-pair-row">'
    + '<label>Boost terms' + btTipHTML + '</label>'
    + '<div class="field-col boost-pair-fields">'
    +   '<textarea id="cfg__boost_terms" rows="4" class="boost-terms-field">' + btVal + '</textarea>'
    +   '<div class="boost-weight-col">'
    +     '<label for="cfg__boost_weight" class="boost-weight-label">Weight' + bwTipHTML + '</label>'
    +     '<input id="cfg__boost_weight" type="number" step="any" value="' + bwVal + '">'
    +   '</div>'
    + '</div>'
    + '</div>';
  return html;
}

/* -------- Main config editor renderer -------- */
function renderConfigEditor() {
  var content = document.getElementById('modal-content');
  if (!content) return;

  // Build set of keys that are handled by explicit sections (suppress from fallback)
  var renderedKeys = {};
  SOURCE_PAGINATION_ALL.forEach(function(k) { renderedKeys[k] = true; });
  renderedKeys['sources'] = true;
  // Scoring section keys
  ['scoring_method', '____comment_scoring_method', 'match_threshold', 'boost_terms', 'boost_weight']
    .forEach(function(k) { renderedKeys[k] = true; });
  // Hidden keys (suppressed from fallback)
  Object.keys(FIELD_META).forEach(function(k) {
    if (FIELD_META[k].hidden) renderedKeys[k] = true;
  });

  var html = '';

  GROUPS.forEach(function(group) {
    html += '<div class="config-group-header">' + group.title + '</div>';

    if (group.type === 'credentials') {
      html += renderCredentialsSection();
    } else if (group.type === 'sources') {
      html += renderSourcesSection();
    } else if (group.type === 'scoring') {
      html += renderScoringSection();
    } else {
      group.keys.forEach(function(key) {
        renderedKeys[key] = true;
        var meta = FIELD_META[key] || {};
        if (meta.hidden) return;  // no DOM output for hidden fields
        if (!configData.hasOwnProperty(key)) return;
        html += renderField(key, configData[key], [key], meta);
      });
    }
  });

  // Fallback: any keys in configData not already rendered
  var unknownKeys = Object.keys(configData).filter(function(k) { return !renderedKeys[k]; });
  if (unknownKeys.length > 0) {
    html += '<div class="config-group-header">Other</div>';
    unknownKeys.forEach(function(k) {
      html += renderField(k, configData[k], [k]);
    });
  }

  content.innerHTML = html;

  /* Wire tooltip hover via event delegation (re-registered each render) */
  content.removeEventListener('mouseover', _tipOver);
  content.removeEventListener('mouseout', _tipOut);
  content.addEventListener('mouseover', _tipOver);
  content.addEventListener('mouseout', _tipOut);
}

/* -------- SerpAPI cost: generic recalculation using onscreen values -------- */
function collectCurrentConfigFlat() {
  var inputs = document.querySelectorAll('#modal-content input, #modal-content textarea, #modal-content select');
  var flat = {};
  inputs.forEach(function(el) {
    var val = (el.type === 'checkbox') ? el.checked : el.value;
    flat[el.id] = val;
  });
  return flat;
}

function calculateSerpApiCost() {
  var flat = collectCurrentConfigFlat();
  var remotePages = parseInt(flat['cfg__serpapi_max_pages']) || 0;
  var localPages = parseInt(flat['cfg__serpapi_local_max_pages']) || 0;
  var searchRemote = flat['cfg__search_remote'] === true;
  var searchLocal = flat['cfg__search_local'] === true;

  var kwEl = document.querySelector('#cfg__keywords');
  var kwCount = kwEl ? kwEl.value.split(',').filter(function(s) { return s.trim(); }).length : 1;

  var remoteCalls = searchRemote ? remotePages * kwCount : 0;
  var localCalls = searchLocal ? localPages * kwCount : 0;
  var totalCalls = remoteCalls + localCalls;

  var passDesc = (searchLocal && searchRemote) ? 'dual-pass' : searchLocal ? 'local only' : searchRemote ? 'remote only' : 'none';
  var costEl = document.getElementById('serpapiCost');
  if (!costEl) return;

  var lines = ['Estimated SerpAPI usage: ~' + totalCalls + ' credits per run (' + passDesc + ')'];
  if (searchRemote && remoteCalls > 0) {
    lines.push('  Remote: ' + remotePages + ' page' + (remotePages !== 1 ? 's' : '') + ' × ' + kwCount + ' keywords = ' + remoteCalls + ' credits · up to ' + (remoteCalls * 10) + ' results');
  }
  if (searchLocal && localCalls > 0) {
    lines.push('  Local:  ' + localPages + ' page' + (localPages !== 1 ? 's' : '') + ' × ' + kwCount + ' keywords = ' + localCalls + ' credits · up to ' + (localCalls * 10) + ' results');
  }
  lines.push('Note: SerpAPI caches identical requests for 1 hr — repeated runs may use fewer credits.');
  var htmlLines = lines.map(function(line, i) {
    if (i === 0) return '<span style="font-weight:bold;text-decoration:underline;">' + line + '</span>';
    return '<span style="font-weight:normal;">' + line + '</span>';
  });
  costEl.innerHTML = htmlLines.join('<br>');
}

/* -------- Get active SerpAPI key from credential rows -------- */
function getActiveSerpApiKey() {
  var rows = document.querySelectorAll('.credential-row[data-scraper="serpapi"]');
  for (var i = 0; i < rows.length; i++) {
    var nameField = rows[i].querySelector('.cred-name');
    if (nameField && nameField.value.trim().toLowerCase() === 'active') {
      var keyField = rows[i].querySelector('.cred-field[data-field="key"]');
      return keyField ? keyField.value.trim() : '';
    }
  }
  return '';
}

/* -------- Fetch SerpAPI remaining (one-time on open) -------- */
async function fetchSerpApiRemaining() {
  var target = document.getElementById('serpapiRemaining');
  try {
    var apiKey = getActiveSerpApiKey();
    if (!apiKey) {
      if (target) target.innerText = 'SerpAPI Requests Remaining: (no key)';
      return;
    }
    var resp = await fetch(apiUrl('serpapi_status', { api_key: apiKey }));
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    var data = await resp.json();
    if (data.success && data.searches_left !== undefined) {
      target.innerText = 'SerpAPI Requests Remaining: ' + data.searches_left + ' (' + (data.plan_name || 'unknown plan') + ')';
    } else if (data.success) {
      target.innerText = 'SerpAPI Requests Remaining: unexpected response';
    } else {
      target.innerText = 'SerpAPI Requests Remaining: error';
    }
  } catch (err) {
    if (target) target.innerText = 'SerpAPI Requests Remaining: unavailable';
  }
}

/* -------- Helper function to build API URLs -------- */
function apiUrl(endpoint, params) {
  params = params || {};
  var url = API_BASE + "?endpoint=" + endpoint;
  if (API_ENV === 'DEV') url += "&env=DEV";
  var queryString = Object.keys(params).map(function(k) { return k + '=' + encodeURIComponent(params[k]); }).join('&');
  if (queryString) url += "&" + queryString;
  return url;
}

/* -------- Helper: Collect config from form -------- */
function collectConfigFromForm() {
  var result = {};

  function setNestedValue(obj, pathArray, value) {
    var current = obj;
    for (var i = 0; i < pathArray.length - 1; i++) {
      var key = pathArray[i];
      if (!current[key]) current[key] = {};
      current = current[key];
    }
    current[pathArray[pathArray.length - 1]] = value;
  }

  // :not(:disabled) — skip disabled pagination inputs (source unchecked); they should not be saved as 0
  var inputs = document.querySelectorAll(
    '#modal-content input[id^="cfg__"]:not(:disabled), ' +
    '#modal-content textarea[id^="cfg__"]:not(:disabled), ' +
    '#modal-content select[id^="cfg__"]:not(:disabled)'
  );

  inputs.forEach(function(el) {
    var id = el.id;
    if (!id.startsWith('cfg__')) return;

    var fullPath = id.substring(5); // Remove 'cfg__' prefix
    var pathArray;
    if (fullPath.startsWith('_')) {
      pathArray = [fullPath];
    } else {
      pathArray = fullPath.split('__');
    }
    var value;

    if (el.type === 'checkbox') {
      value = el.checked;
    } else if (el.type === 'number') {
      value = parseFloat(el.value);
      if (isNaN(value)) value = 0;
    } else if (el.tagName === 'TEXTAREA') {
      var text = el.value.trim();
      value = text ? text.split(',').map(function(s) { return s.trim(); }).filter(function(s) { return s; }) : [];
    } else {
      value = el.value;
    }

    setNestedValue(result, pathArray, value);
  });

  // Restore always-disabled fields (e.g. serpapi_results_per_page) from configData —
  // they are excluded by :not(:disabled) above but must be preserved in saved JSON
  document.querySelectorAll('#modal-content [data-always-disabled]').forEach(function(el) {
    var fullPath = el.id.substring(5);
    var pathArray = fullPath.startsWith('_') ? [fullPath] : fullPath.split('__');
    var cfgKey = pathArray[pathArray.length - 1];
    if (configData[cfgKey] !== undefined) setNestedValue(result, pathArray, configData[cfgKey]);
  });

  // remote and ____comment_scoring_method are stripped — do NOT re-inject them
  // ____comment_keywords is now a visible textarea — collected from DOM like any other field

  // Preserve pagination fields for disabled sources — they are rendered as disabled inputs
  // and skipped by :not(:disabled) above, but must survive save/reload cycles
  SOURCE_PAGINATION_ALL.forEach(function(pkey) {
    if (result[pkey] !== undefined) return; // already collected (source was enabled)
    var el = document.getElementById('cfg__' + pkey);
    if (el) {
      result[pkey] = (el.type === 'number') ? parseFloat(el.value) : el.value;
    } else if (configData[pkey] !== undefined) {
      result[pkey] = configData[pkey];
    } else {
      result[pkey] = SOURCE_PAGINATION_DEFAULTS[pkey];
    }
  });

  // Collect credentials from the custom credential rows (not cfg__ inputs)
  result.credentials = collectCredentials();

  return result;
}

/* -------- Load Previous Button -------- */
document.getElementById('loadPrevBtn').onclick = async function() {
  try {
    var response = await fetch(apiUrl('load_previous', { user: userName, test_mode: isTestMode }));
    var data = await response.json();

    if (data.success) {
      Object.keys(configData).forEach(function(k) { delete configData[k]; });
      Object.assign(configData, data.config);
      renderConfigEditor();
      calculateSerpApiCost();
      alert('Previous configuration loaded successfully!');
    } else {
      alert('Error: ' + (data.error || 'Failed to load previous config'));
    }
  } catch (err) {
    alert('Error loading previous config: ' + err.message);
  }
};

/* -------- Undo Changes Button -------- */
document.getElementById('undoBtn').onclick = function() {
  renderConfigEditor();
  calculateSerpApiCost();
  alert('Changes reverted to last loaded configuration');
};

/* -------- Save Button -------- */
document.getElementById('saveBtn').onclick = async function() {
  // Validate: at least one search pass must be enabled
  var srRemote = document.getElementById('cfg__search_remote');
  var srLocal  = document.getElementById('cfg__search_local');
  if (srRemote && srLocal && !srRemote.checked && !srLocal.checked) {
    alert('At least one search pass (Search Remote or Search Local) must be enabled.');
    return;
  }

  var saveBtn = document.getElementById('saveBtn');
  var originalText = saveBtn.innerHTML;

  saveBtn.disabled = true;
  saveBtn.innerHTML = '\uD83D\uDCBE Saving...';

  var config = collectConfigFromForm();

  try {
    var response = await fetch(apiUrl('save'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        user: userName,
        config: config,
        test_mode: isTestMode
      })
    });

    if (!response.ok) {
      throw new Error('HTTP ' + response.status + ': ' + response.statusText);
    }

    var data = await response.json();

    if (data.success) {
      Object.keys(configData).forEach(function(k) { delete configData[k]; });
      Object.assign(configData, config);
      saveBtn.innerHTML = '\u2705 Saved!';
      setTimeout(function() {
        saveBtn.innerHTML = originalText;
        saveBtn.disabled = false;
      }, 2000);
      alert('\u2705 Configuration saved successfully!\n\n' + data.backup);
    } else {
      saveBtn.innerHTML = '\u274C Failed';
      setTimeout(function() {
        saveBtn.innerHTML = originalText;
        saveBtn.disabled = false;
      }, 2000);
      alert('\u274C Error saving config:\n\n' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    saveBtn.innerHTML = '\u274C Error';
    setTimeout(function() {
      saveBtn.innerHTML = originalText;
      saveBtn.disabled = false;
    }, 2000);
    alert('\u274C Error saving configuration:\n\n' + err.message + '\n\nMake sure API server is running.\nTest: /api.php?endpoint=health');
  }
};

/* -------- Show/hide progress section -------- */
function showProgressSection() {
  document.getElementById('modal-content').style.display = 'none';
  document.getElementById('modal-bottom').style.display = 'none';
  document.getElementById('progress-section').style.display = 'block';
}

function hideProgressSection() {
  document.getElementById('progress-section').style.display = 'none';
  document.getElementById('modal-content').style.display = 'block';
  document.getElementById('modal-bottom').style.display = 'block';
}

function appendJobOutput(text) {
  var outputDiv = document.getElementById('job-output');
  outputDiv.textContent += text + '\n';
  outputDiv.scrollTop = outputDiv.scrollHeight;
}

function updateJobStatus(status) {
  document.getElementById('job-status').textContent = status;
}

var jobRunning = false;
var jobPollingInterval = null;
var lastDisplayedTimestamp = '';

function pollJobStatus() {
  fetch(apiUrl('job_status'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user: userName, test_mode: isTestMode })
  })
  .then(function(response) { return response.json(); })
  .then(function(data) {
    if (data.success && data.status) {
      var status = data.status;

      if (status.messages && Array.isArray(status.messages)) {
        var messages = status.messages;
        for (var i = 0; i < messages.length; i++) {
          var msg = messages[i];
          if (msg.timestamp > lastDisplayedTimestamp) {
            appendJobOutput('[' + msg.timestamp + '] ' + msg.text);
            lastDisplayedTimestamp = msg.timestamp;
          }
        }
      } else if (status.message) {
        if (!window._lastFallbackMsg || window._lastFallbackMsg !== status.message) {
          appendJobOutput('[' + new Date().toLocaleTimeString() + '] ' + status.message);
          window._lastFallbackMsg = status.message;
        }
      }

      if (status.state === 'complete') {
        stopPolling();
        updateJobStatus('Complete');
        appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2713 SUCCESS! Job completed.');

        var startBtn = document.getElementById('startJobBtn');
        startBtn.disabled = false;
        startBtn.textContent = '\u25B6 Start Job Search';
        var cancelBtn = document.getElementById('cancelJobBtn');
        cancelBtn.textContent = '\u25C0 Back to Config';
        cancelBtn.style.background = '';
        jobRunning = false;
        lastDisplayedTimestamp = '';

        setTimeout(function() {
          document.getElementById('editModal').style.display = 'none';
          location.reload();
        }, 1500);
      } else if (status.state === 'error') {
        stopPolling();
        updateJobStatus('Error');
        appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2717 ERROR! Job failed.');

        var startBtn = document.getElementById('startJobBtn');
        startBtn.disabled = false;
        startBtn.textContent = '\u25B6 Retry Job Search';
        var cancelBtn = document.getElementById('cancelJobBtn');
        cancelBtn.textContent = '\u25C0 Back to Config';
        cancelBtn.style.background = '';
        jobRunning = false;
        lastDisplayedTimestamp = '';
      }
    }
  })
  .catch(function(err) {
    console.error('Polling error:', err);
  });
}

function stopPolling() {
  if (jobPollingInterval) {
    clearInterval(jobPollingInterval);
    jobPollingInterval = null;
  }
}

function startJobSearch() {
  if (jobRunning) {
    alert('Job search is already running!');
    return;
  }

  jobRunning = true;
  lastDisplayedTimestamp = '';
  window._lastFallbackMsg = null;
  var startBtn = document.getElementById('startJobBtn');
  startBtn.disabled = true;
  startBtn.textContent = '\u231B Running...';
  var cancelBtn = document.getElementById('cancelJobBtn');
  cancelBtn.textContent = '\u2716 Cancel Job';
  cancelBtn.style.background = '#dc3545';

  updateJobStatus('Sending...');
  document.getElementById('job-output').textContent = '';
  appendJobOutput('[' + new Date().toLocaleTimeString() + '] Starting job search for ' + userName);
  appendJobOutput('Sending request to API server...');

  fetch(apiUrl('run'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user: userName, test_mode: isTestMode })
  })
  .then(function(response) { return response.json(); })
  .then(function(data) {
    if (data.success && data.job_started) {
      updateJobStatus('Running...');
      appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2713 Job accepted by API server (PID: ' + data.pid + ')');
      appendJobOutput('Job is now running on the server. Checking status every 5 seconds...');
      jobPollingInterval = setInterval(pollJobStatus, 5000);
    } else if (data.success) {
      updateJobStatus('Complete');
      appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2713 SUCCESS!');
      if (data.output) {
        appendJobOutput('--- Job Search Output ---');
        appendJobOutput(data.output);
      }
      appendJobOutput('\u2713 Job completed! Refreshing results...');
      stopPolling();
      startBtn.disabled = false;
      startBtn.textContent = '\u25B6 Start Job Search';
      jobRunning = false;
      setTimeout(function() {
        document.getElementById('editModal').style.display = 'none';
        location.reload();
      }, 1500);
    } else {
      updateJobStatus('Error');
      appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2717 ERROR!');
      appendJobOutput('Error: ' + (data.error || 'Unknown error'));
      if (data.stderr) {
        appendJobOutput('Details:');
        appendJobOutput(data.stderr);
      }
      startBtn.disabled = false;
      startBtn.textContent = '\u25B6 Retry Job Search';
      jobRunning = false;
    }
  })
  .catch(function(err) {
    updateJobStatus('Connection Error');
    appendJobOutput('[' + new Date().toLocaleTimeString() + '] \u2717 CONNECTION ERROR!');
    appendJobOutput('Error: ' + err.message);
    appendJobOutput('Make sure the API server is running (check /api.php?endpoint=health)');
    startBtn.disabled = false;
    startBtn.textContent = '\u25B6 Retry Job Search';
    jobRunning = false;
  });
}

/* -------- Run button wiring -------- */
document.getElementById('runBtn').onclick = function() {
  showProgressSection();
};
