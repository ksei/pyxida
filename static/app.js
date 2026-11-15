const $ = (selector) => document.querySelector(selector);
let state = null;
let busy = false;
let selectedFile = null;
let portraitImage = null;
let appearanceProfile = null;
let appearanceDirty = false;
const LANGUAGES = ['English', 'German', 'French', 'Italian'];
const FINAL = ['hired', 'rejected', 'no_response', 'offer_declined', 'withdrawn'];
const REACHED_INTERVIEW = ['interview', 'offer', 'hired', 'offer_declined'];

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

function el(tag, className, ...children) {
  const result = node(tag, undefined, className);
  result.append(...children.filter(c => c !== null && c !== undefined && c !== false));
  return result;
}

function button(label, onClick, className = 'secondary small-button') {
  const b = node('button', label, className);
  b.type = 'button'; b.disabled = busy;
  b.addEventListener('click', onClick);
  return b;
}

function select(options, value, label) {
  const s = document.createElement('select');
  for (const option of options) {
    const [v, text] = Array.isArray(option) ? option : [option, option];
    const o = node('option', text); o.value = v; s.append(o);
  }
  if (value !== undefined) s.value = value;
  if (label) s.setAttribute('aria-label', label);
  return s;
}

function input(type, value, placeholder, label) {
  const i = document.createElement('input');
  i.type = type; i.value = value || ''; if (placeholder) i.placeholder = placeholder;
  if (label) i.setAttribute('aria-label', label);
  return i;
}

function labeled(text, control) {
  const l = node('label', text); l.append(control); return l;
}

function todayISO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Zurich' });
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const STORAGE_KEY = 'pyxida-profile';
const VIEWS = { profile: 'Profile', jobs: 'Jobs', applications: 'Applications', pipeline: 'Pipeline & interviews', growth: 'Skills & growth', settings: 'Settings' };
const openState = new Set();
const seenDetails = new Set();
let careerRendered = null;
let loginTimer = null;

function store(action, value) {
  try {
    if (action === 'get') return localStorage.getItem(STORAGE_KEY) || localStorage.getItem('job-applicator-profile');
    if (action === 'set') localStorage.setItem(STORAGE_KEY, value);
    if (action === 'remove') { localStorage.removeItem(STORAGE_KEY); localStorage.removeItem('job-applicator-profile'); }
  } catch { /* storage unavailable: the profile picker still works */ }
  return null;
}

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
  });
  let result;
  try { result = await response.json(); } catch { throw new Error('The server returned an unexpected response. Please retry.'); }
  if (response.status === 401 && result.code === 'signed_out') { location.href = '/login'; throw new Error('Sign in to continue.'); }
  if (!response.ok) throw new Error(result.error || 'The request failed. Please retry.');
  if (response.status === 202 && result.task) return waitForTask(result.task);
  return result;
}

// Long Codex runs continue on the server; the browser polls so proxies never time out.
async function waitForTask(id) {
  let failures = 0;
  for (;;) {
    await sleep(2000);
    let task;
    try { task = await api(`/api/task?id=${encodeURIComponent(id)}`); failures = 0; }
    catch (e) { if (++failures > 4 || /no longer tracked/.test(e.message)) throw e; continue; }
    if (task.status === 'done') return task.result;
    if (task.status === 'error') throw new Error(task.error);
    const text = $('#activity-text');
    if (text.dataset.base && task.elapsed >= 10) {
      const m = Math.floor(task.elapsed / 60), sec = task.elapsed % 60;
      text.textContent = `${text.dataset.base} · ${m ? `${m} min ` : ''}${sec}s`;
    }
  }
}

function setActivity(message) {
  $('#activity-text').dataset.base = message;
  $('#activity-text').textContent = message;
}

function currentView() {
  const view = location.hash.replace(/^#\/?/, '');
  return view in VIEWS ? view : 'profile';
}

function showView() {
  const view = currentView();
  document.querySelectorAll('.view').forEach(v => { v.hidden = v.dataset.view !== view; });
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === view));
  $('#view-title').textContent = VIEWS[view];
  document.title = `Pyxida · ${VIEWS[view]}`;
}

function go(view) {
  if (currentView() !== view) location.hash = '#/' + view;
  else showView();
  window.scrollTo({ top: 0 });
}
window.addEventListener('hashchange', () => { showView(); window.scrollTo({ top: 0 }); });

function remembered(details, key, defaultOpen = false) {
  if (!seenDetails.has(key)) { seenDetails.add(key); if (defaultOpen) openState.add(key); }
  details.open = openState.has(key);
  details.addEventListener('toggle', () => { details.open ? openState.add(key) : openState.delete(key); });
  return details;
}

async function readFile(file, limit, label) {
  if (file.size > limit) throw new Error(`Choose a ${label} smaller than ${Math.round(limit / 1048576)} MB.`);
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(new Error('Could not read this file.')); reader.readAsDataURL(file);
  });
}

function error(message) {
  $('#error').textContent = message;
  $('#error').hidden = !message;
  if (message) $('#error').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function jobStale(job) {
  return (job.revision ?? state.search_revision) !== state.revision;
}

function selectedJobs() {
  const jobs = state?.shortlist?.jobs || [];
  return (state?.selected || []).map(id => jobs.find(j => j.id === id)).filter(Boolean);
}

function controls() {
  document.querySelectorAll('button, input, textarea, select').forEach(el => el.disabled = busy);
  $('#search-button').disabled = busy || !state;
  $('#upskill-button').disabled = busy || !state;
  const remaining = pendingApplications().length;
  const stale = selectedJobs().some(jobStale);
  $('#prepare').disabled = busy || !state || !remaining || stale;
  $('#prepare').textContent = state?.selected.length && !remaining
    ? 'Selected applications are ready'
    : `Prepare ${remaining || 'selected'} application${remaining === 1 ? '' : 's'} ↗`;
  $('#delete-profile').hidden = !state;
  $('#save-appearance').disabled = busy || !state;
  $('#remove-photo').disabled = busy || !state?.photo;
  $('#photo-file').disabled = busy || !state;
  for (const id of ['#photo-zoom', '#photo-x', '#photo-y']) $(id).disabled = busy || !portraitImage;
  for (const id of ['#photo-on-cv', '#photo-on-letter']) $(id).disabled = busy || !(portraitImage || state?.photo);
  for (const id of ['#evaluate-url', '#evaluate-text', '#expand-urls', '#document-file', '#document-type']) $(id).disabled = busy || !state;
}

function pendingApplications() {
  const language = $('#draft-language').value;
  return (state?.selected || []).filter(id => {
    const draft = state.drafts[id];
    return !draft || draft.revision !== state.revision || draft.language !== language;
  });
}

async function run(message, task) {
  if (busy) return;
  busy = true;
  error('');
  setActivity(message);
  $('#activity').hidden = false;
  controls();
  try { await task(); } catch (e) { error(e.message); }
  finally { busy = false; $('#activity').hidden = true; controls(); }
}

function act(message, route, data, after) {
  return run(message, async () => {
    saveState(await api(route, { id: state.id, ...data }));
    if (after) after();
  });
}

function preferences() {
  return { cities: $('#cities').value, work_style: $('#work-style').value, salary: $('#salary').value,
    constraints: $('#constraints').value, focus: $('#focus').value, permit: $('#permit').value, depth: $('#depth').value };
}

function saveState(next) {
  state = next;
  if (state.id) store('set', state.id);
  render();
}

function bullets(values, className) {
  const ul = node('ul', undefined, className);
  values.forEach(value => ul.append(node('li', value)));
  return ul;
}

function evidence(values) {
  const fragment = document.createDocumentFragment();
  values.forEach(value => fragment.append(node('blockquote', value, 'evidence-quote')));
  return fragment;
}

function link(url, label, className) {
  const a = node('a', label, className);
  let parsed;
  try { parsed = new URL(url, window.location.origin); } catch { return node('span', label); }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin === window.location.origin) return node('span', label);
  a.href = parsed.href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
}

function chip(text, className = '') {
  return node('span', text, 'chip ' + className);
}

function copyButton(getText) {
  return button('Copy', async e => {
    try { await navigator.clipboard.writeText(getText()); e.target.textContent = 'Copied'; }
    catch { e.target.textContent = 'Copy failed'; }
  }, 'text-button');
}

// Shortlist -----------------------------------------------------------------
function deadlineChip(job) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(job.deadline_date || '')) return null;
  const days = Math.round((Date.parse(job.deadline_date) - Date.parse(todayISO())) / 86400000);
  if (days < 0) return chip('Deadline passed', 'danger-chip');
  if (days <= 7) return chip(days === 0 ? 'Closes today' : `Closes in ${days} day${days === 1 ? '' : 's'}`, 'urgent');
  return chip(`Closes ${new Date(job.deadline_date).toLocaleDateString('en-CH')}`);
}

function gateChips(job) {
  const chips = [];
  const names = { language_gate: 'Language', eligibility: 'Work permit', location_check: 'Location' };
  for (const [key, name] of Object.entries(names)) {
    const value = job[key];
    if (!value || value === 'PASS') continue;
    const text = value === 'UNVERIFIED' ? `${name}: unverified` : `${name}: ${value === 'FAIL' ? 'fails' : 'check'}`;
    chips.push(chip(text, value === 'FAIL' ? 'danger-chip' : 'flag-chip'));
  }
  return chips;
}

function scoreTable(job) {
  const table = node('table', undefined, 'score-table');
  const rows = [['Skills', 'skills', '30%'], ['Experience', 'experience', '25%'], ['Culture', 'culture', '15%'], ['Career', 'career', '30%']];
  for (const [label, key, weight] of rows) {
    const value = job.scores[key];
    const bar = el('div', 'bar', node('span'));
    bar.firstChild.style.width = `${value}%`;
    table.append(el('tr', '', node('td', label), el('td', 'bar-cell', bar), node('td', `${value}`), node('td', weight, 'small')));
  }
  return table;
}

function contactsLink(job) {
  const keywords = `${job.company} ${job.title}`.trim();
  return link(`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(keywords)}`, 'Find people to ask ↗');
}

function renderJobCard(job, { vetoed = false } = {}) {
  const chosen = state.selected.includes(job.id);
  const card = node('article', undefined, 'job-card' + (chosen ? ' selected' : '') + (vetoed ? ' vetoed' : ''));
  const top = node('div', undefined, 'job-top');
  const heading = node('div');
  const badges = el('div', 'badges',
    node('span', job.overall != null ? `${job.fit} match · ${job.overall}/100` : `${job.fit} match`, 'fit ' + (job.fit || '').toLowerCase()),
    job.new && chip('New'), job.source === 'manual' && chip('Your posting'), deadlineChip(job), ...gateChips(job));
  heading.append(badges, node('h4', job.title), node('p', `${job.company} · ${job.location}`, 'company'));
  top.append(heading);
  if (!vetoed) {
    const label = node('label');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = chosen; checkbox.disabled = busy;
    checkbox.setAttribute('aria-label', `Select ${job.title} at ${job.company}`);
    checkbox.addEventListener('change', () => run('Saving your selection…', async () => {
      const ids = new Set(state.selected);
      checkbox.checked ? ids.add(job.id) : ids.delete(job.id);
      try { saveState(await api('/api/selection', { id: state.id, selected: [...ids] })); }
      finally { renderJobs(); }
    }));
    label.append(checkbox, node('span', 'Select'));
    top.append(label);
  }
  card.append(top);
  if (job.vetoes?.length) card.append(bullets(job.vetoes, 'veto-list'));
  if (job.integrity) {
    const notice = node('div', undefined, 'notice' + (job.integrity.warning ? ' error' : ' quiet'));
    notice.append(node('strong', 'Posting check: '), document.createTextNode(job.integrity.verdict));
    if (job.integrity.findings.instructions.length) notice.append(bullets(job.integrity.findings.instructions.map(l => `“${l}”`)));
    card.append(notice);
  }
  if (jobStale(job)) card.append(node('p', 'Your profile changed after this evaluation. Search again or evaluate this posting again before drafting.', 'notice'));
  card.append(node('p', job.why, 'why'));
  const meta = node('div', undefined, 'job-meta');
  [job.work_style, job.language, job.salary].forEach(value => value && meta.append(node('span', value)));
  card.append(meta);
  const detail = node('details', undefined, 'job-detail');
  detail.append(node('summary', 'Fit breakdown, requirements, gaps & CV evidence'));
  if (job.scores) detail.append(node('h5', 'FIT SCORES'), scoreTable(job));
  if (job.language_note) detail.append(node('h5', 'LANGUAGES'), node('p', job.language_note, 'small'));
  if (job.eligibility_note) detail.append(node('h5', 'WORK AUTHORIZATION'), node('p', job.eligibility_note, 'small'));
  detail.append(node('h5', 'LISTING REQUIREMENTS'), bullets(job.requirements));
  if (job.keywords?.length) detail.append(node('h5', 'LISTING KEYWORDS'), el('div', 'chips', ...job.keywords.map(k => node('span', k))));
  detail.append(node('h5', 'GAPS & UNKNOWNS'), bullets(job.gaps.length ? job.gaps : ['No material gaps identified; review the listing.']));
  detail.append(node('h5', 'FROM YOUR CV / DISCUSSION'), job.evidence.length ? evidence(job.evidence) : node('p', 'No exact CV excerpt was matched.', 'small'));
  if (job.questions_for_employer?.length) detail.append(node('h5', 'WORTH ASKING THE EMPLOYER BEFORE APPLYING'), bullets(job.questions_for_employer));
  detail.append(node('p', `Deadline: ${job.deadline}`, 'small'));
  card.append(detail);
  const footer = node('div', undefined, 'job-footer');
  const links = el('div', 'footer-links', job.url ? link(job.url, 'Open listing ↗') : node('span', 'Pasted posting'), contactsLink(job));
  if (!vetoed) links.append(button('Dismiss', () => act('Dismissing this listing…', '/api/dismiss', { job_id: job.id }), 'text-button'));
  const status = job.listing_status === 'Open' ? 'Reported open' : job.listing_status === 'Closed' ? 'Reported closed' : 'Status unclear';
  footer.append(links, node('span', `${status} · checked ${new Date(job.checked_at).toLocaleDateString('en-CH', { timeZone: 'Europe/Zurich' })}`));
  card.append(footer);
  return card;
}

function renderJobs() {
  $('#jobs').replaceChildren();
  if (!state?.shortlist) {
    const empty = node('div', undefined, 'empty');
    empty.append(node('span', '↗'), node('p', 'Your shortlist will appear here after we understand your profile.'));
    $('#jobs').append(empty);
    return;
  }
  const filter = $('#fit-filter').value;
  const jobs = state.shortlist.jobs.filter(j => !filter || (filter === 'selected' ? state.selected.includes(j.id) : j.fit === filter));
  if (!jobs.length) $('#jobs').append(node('div', 'No listings in this view. Change the filter or broaden your preferences and search again.', 'empty'));
  jobs.forEach(job => $('#jobs').append(renderJobCard(job)));
  const vetoed = state.shortlist.vetoed || [];
  $('#vetoed-container').hidden = !vetoed.length;
  $('#vetoed-summary').textContent = `Filtered out by hard gates (${vetoed.length})`;
  $('#vetoed').replaceChildren(...vetoed.map(job => renderJobCard(job, { vetoed: true })));
  const dismissed = Object.entries(state.history || {}).filter(([, h]) => h.status === 'dismissed');
  $('#dismissed-container').hidden = !dismissed.length;
  $('#dismissed-summary').textContent = `Dismissed listings (${dismissed.length}), skipped in future searches`;
  $('#dismissed').replaceChildren(...dismissed.map(([id, h]) => el('li', '', node('span', `${h.job.title} · ${h.job.company}`),
    button('Restore', () => act('Restoring this listing…', '/api/dismiss', { job_id: id, restore: true }), 'text-button'))));
}

function renderPortals() {
  const portals = state?.portals || [];
  const coverage = Object.fromEntries((state?.shortlist?.portal_coverage || []).map(c => [c.domain, c]));
  $('#portal-list').replaceChildren(...portals.map(p => {
    const box = document.createElement('input');
    box.type = 'checkbox'; box.checked = p.enabled; box.disabled = busy;
    box.addEventListener('change', () => act('Updating portals…', '/api/portals', { action: 'toggle', domain: p.domain, enabled: box.checked }));
    const c = coverage[p.domain];
    const health = !c ? '' : c.sources ? `${c.sources} source${c.sources === 1 ? '' : 's'}, ${c.jobs} on shortlist` : 'no results last search';
    const row = el('li', c && !c.sources && p.enabled ? 'portal quiet-portal' : 'portal',
      el('label', '', box, node('span', p.name)), node('span', p.domain, 'small'), health && node('span', health, 'small health'));
    if (!p.builtin) row.append(button('Remove', () => act('Removing portal…', '/api/portals', { action: 'remove', domain: p.domain }), 'text-button danger'));
    return row;
  }));
}

// Applications ----------------------------------------------------------------
function statusOptions() {
  return Object.entries(state.pipeline.statuses);
}

function checksBlock(id, draft) {
  const detail = node('details', undefined, 'checks');
  const items = draft.checks?.items || [];
  const failing = items.filter(i => i.status === 'fail').length;
  const warnings = items.filter(i => i.status === 'warn').length;
  detail.open = failing > 0;
  detail.append(node('summary', `Quality checks · ${failing ? `${failing} to fix` : 'no blocking issues'}${warnings ? ` · ${warnings} to review` : ''}`));
  const list = node('ul', undefined, 'check-list');
  items.forEach(i => list.append(el('li', 'check-' + i.status, node('strong', i.label), document.createTextNode(' ' + i.detail))));
  detail.append(list);
  const kw = draft.checks?.keywords;
  if (kw && (kw.found.length || kw.missing.length)) {
    detail.append(node('h5', 'LISTING KEYWORDS IN YOUR CV'),
      el('div', 'chips', ...kw.found.map(k => chip('✓ ' + k, 'ok-chip')), ...kw.missing.map(k => chip('– ' + k, 'flag-chip'))));
  }
  detail.append(button('Run checks again', () => act('Checking documents…', '/api/checks', { job_id: id })));
  return detail;
}

function reviewBlock(review) {
  const detail = node('details', undefined, 'review');
  detail.append(node('summary', `Independent review${review.applied ? ' · revisions applied' : ' · not applied'}`));
  detail.append(node('p', review.summary, 'small'));
  if (review.issues.length) {
    const list = node('ul', undefined, 'issue-list');
    review.issues.forEach(i => list.append(el('li', '', chip(i.severity, 'sev-' + i.severity), node('strong', ` ${i.document.replace('_', ' ')}: `), document.createTextNode(`${i.issue} → ${i.fix}`))));
    detail.append(list);
  }
  if (review.stretch_claims.length) detail.append(node('h5', 'CLAIMS TO CONFIRM YOU CAN DEFEND'), bullets(review.stretch_claims));
  if (review.company_points.length) {
    const list = node('ul');
    review.company_points.forEach(p => list.append(el('li', '', document.createTextNode(p.point + ' '), link(p.url, 'source ↗'))));
    detail.append(node('h5', 'VERIFIED EMPLOYER FACTS'), list);
  }
  return detail;
}

function formBlock(id, draft) {
  const detail = node('details', undefined, 'form-answers');
  detail.append(node('summary', 'Application form questions'));
  const area = node('textarea'); area.rows = 4; area.disabled = busy;
  area.placeholder = 'One question per line. Add a character limit after a |, e.g.\nWhy do you want to join us? | 1000\nNotice period';
  area.setAttribute('aria-label', 'Application form questions');
  const fields = draft.form_answers?.answers;
  if (fields) area.value = fields.map(f => f.limit ? `${f.label} | ${f.limit}` : f.label).join('\n');
  detail.append(area, button('Draft answers ↗', () => {
    const parsed = area.value.split('\n').map(l => l.trim()).filter(Boolean).map(line => {
      const [label, limit] = line.split(/\s*\|\s*(?=\d+\s*$)/);
      return { label: label.trim(), limit: limit ? parseInt(limit, 10) : 0 };
    });
    act('Codex is drafting form answers from your evidence…', '/api/form-answers', { job_id: id, fields: parsed, language: draft.language });
  }));
  for (const answer of fields || []) {
    const box = el('div', 'answer', node('h5', answer.label), node('pre', answer.answer));
    const info = answer.limit ? `${answer.length} / ${answer.limit} characters` : `${answer.length} characters`;
    box.append(el('div', 'answer-meta', node('span', info, answer.over_limit ? 'danger' : 'small'), copyButton(() => answer.answer)));
    if (answer.sources.length) { const d = node('details'); d.append(node('summary', 'Evidence'), evidence(answer.sources)); box.append(d); }
    else box.append(node('p', 'No CV source cited; check this answer.', 'small'));
    detail.append(box);
  }
  if (draft.form_answers?.notes) detail.append(node('p', draft.form_answers.notes, 'small'));
  return detail;
}

function trackForm(id, application) {
  const form = node('div', undefined, 'track-form');
  const status = select(statusOptions(), application && application.status !== 'drafted' ? application.status : 'applied', 'Application status');
  const date = input('date', todayISO(), '', 'Date');
  const channel = select([['email', 'Email'], ['portal', 'Portal'], ['linkedin', 'LinkedIn'], ['referral', 'Referral'], ['other', 'Other']], application?.channel || 'email', 'How you applied');
  const contact = input('text', application?.contact, 'Contact person (optional)', 'Contact person');
  form.append(node('span', application ? `Pipeline: ${state.pipeline.statuses[application.status]}` : 'Not tracked', 'small'),
    status, date, channel, contact,
    button('Save to pipeline', () => act('Updating your pipeline…', '/api/track', { job_id: id, status: status.value, date: date.value, channel: channel.value, contact: contact.value })));
  return form;
}

function renderDrafts() {
  $('#drafts').replaceChildren();
  if (!state) return;
  const entries = Object.entries(state.drafts);
  entries.forEach(([id, draft], index) => {
    const card = node('details', undefined, 'draft-card');
    const application = state.applications[id];
    const failing = (draft.checks?.items || []).filter(i => i.status === 'fail').length;
    card.append(el('summary', 'card-summary', el('span', 'summary-main', node('strong', draft.job.title), node('span', ` · ${draft.job.company} · ${draft.language}`, 'small')),
      el('span', 'badges', application && chip(state.pipeline.statuses[application.status], 'status-' + application.status),
        failing ? chip(`${failing} check${failing === 1 ? '' : 's'} to fix`, 'danger-chip') : null,
        draft.revision !== state.revision && chip('Earlier profile', 'flag-chip'))));
    remembered(card, `draft:${id}`, index === entries.length - 1);
    const heading = node('div', undefined, 'draft-heading');
    const title = node('div');
    title.append(node('h3', draft.job.title), node('p', `${draft.job.company} · ${draft.language} · ${draft.reviewed ? 'Reviewed drafts' : 'Drafts'} for review`));
    const download = node('a', 'Download PDF & Word package ↓', 'download');
    download.href = `/api/export?id=${encodeURIComponent(state.id)}&job=${encodeURIComponent(id)}`;
    heading.append(title, download); card.append(heading);
    if (draft.revision !== state.revision) card.append(node('p', 'These drafts use an earlier profile. Prepare them again after refreshing your shortlist.', 'notice'));
    card.append(trackForm(id, state.applications[id]));
    if (draft.checks) card.append(checksBlock(id, draft));
    if (draft.materials.review) card.append(reviewBlock(draft.materials.review));
    for (const [key, label] of [['cv', 'Tailored CV'], ['cover_letter', 'Cover letter'], ['email', 'Application email'], ['notes', 'Review notes']]) {
      const detail = node('details');
      detail.append(node('summary', label));
      if (key === 'cv' || key === 'cover_letter') {
        const base = `/api/document?id=${encodeURIComponent(state.id)}&job=${encodeURIComponent(id)}&kind=${key}`;
        const actions = node('div', undefined, 'document-actions');
        for (const [format, label] of [['pdf', 'Download PDF ↓'], ['docx', 'Download Word ↓']]) {
          const download = node('a', label, 'download');
          download.href = `${base}&format=${format}`; actions.append(download);
        }
        const preview = document.createElement('iframe');
        preview.title = `${label} for ${draft.job.company}`;
        preview.src = `${base}&format=html&v=${encodeURIComponent(state.updated_at)}`;
        preview.loading = 'lazy';
        preview.className = 'document-preview';
        detail.append(actions, preview);
      } else {
        detail.append(node('pre', draft.materials[key]));
      }
      card.append(detail);
    }
    card.append(formBlock(id, draft));
    const trace = node('details');
    trace.append(node('summary', 'Evidence for candidate claims'));
    draft.materials.evidence.forEach(e => trace.append(node('p', e.claim, 'small'), evidence([e.source])));
    card.append(trace);
    if (draft.materials.sources?.length) {
      const ul = node('ul', undefined, 'source-list');
      draft.materials.sources.forEach(s => { const li = node('li'); li.append(link(s.url, s.title)); ul.append(li); });
      card.append(ul);
    }
    $('#drafts').append(card);
  });
  if (!entries.length) $('#drafts').append(node('div', 'Prepared applications appear here.', 'empty'));
}

// Pipeline --------------------------------------------------------------------
function lastActivity(application) {
  const dates = application.events.filter(e => e.status !== 'drafted').map(e => e.date).concat(application.followups.map(f => f.date));
  return dates.sort().pop() || application.created_at;
}

function renderStats() {
  const s = state.pipeline.stats;
  $('#stat-cards').replaceChildren(...[['Tracked', s.tracked], ['Applied', s.applied], ['Interviews', s.interviews],
    ['Offers', s.offers], ['Response rate', `${s.response_rate}%`], ['Follow-ups due', s.followups_due]]
    .map(([label, value]) => el('div', 'stat', node('strong', String(value)), node('span', label))));
  const top = Math.max(s.applied, 1);
  $('#funnel').replaceChildren(...[['Applied', s.applied], ['Interviewed', s.interviews], ['Offers', s.offers]].map(([label, value]) => {
    const bar = el('div', 'bar', node('span'));
    bar.firstChild.style.width = `${Math.round(100 * value / top)}%`;
    return el('div', 'funnel-row', node('span', label), bar, node('span', String(value)));
  }));
  $('#export-csv').href = `/api/tracker.csv?id=${encodeURIComponent(state.id)}`;
  $('#export-report').href = `/api/report?id=${encodeURIComponent(state.id)}`;
}

function followupBlock(id, application, timing) {
  const box = node('div', undefined, 'followup');
  const sent = !['drafted', ...FINAL].includes(application.status);
  const interviewed = REACHED_INTERVIEW.includes(application.status) || application.events.some(e => REACHED_INTERVIEW.includes(e.status));
  const draft = application.draft_followup;
  if (draft) {
    box.append(node('h5', draft.kind === 'followup' ? 'FOLLOW-UP DRAFT' : 'THANK-YOU DRAFT'));
    if (draft.subject) box.append(node('p', `Subject: ${draft.subject}`, 'small'));
    box.append(node('pre', draft.body));
    if (draft.notes) box.append(node('p', draft.notes, 'small'));
    const date = input('date', todayISO(), '', 'Date sent');
    box.append(el('div', 'row', copyButton(() => (draft.subject ? `Subject: ${draft.subject}\n\n` : '') + draft.body), date,
      button('Mark as sent', () => act('Logging your message…', '/api/followup-sent', { job_id: id, date: date.value })),
      button('Discard', () => act('Discarding draft…', '/api/followup-sent', { job_id: id, discard: true }), 'text-button danger')));
    return box;
  }
  if (sent && timing.followups < 2) {
    box.append(button(timing.due ? 'Draft follow-up ↗' : 'Draft follow-up anyway ↗', () =>
      act('Codex is drafting a follow-up from what you sent…', '/api/followup', { job_id: id, kind: 'followup' })));
  } else if (sent) box.append(node('p', 'You have followed up twice. If nothing arrives, consider recording “No response”.', 'small'));
  if (interviewed) {
    const details = node('textarea'); details.rows = 2; details.disabled = busy;
    details.placeholder = 'Interview details to reference (topics discussed, interviewer names)…';
    details.setAttribute('aria-label', 'Interview details for the thank-you note');
    box.append(details, button('Draft thank-you note ↗', () =>
      act('Codex is drafting a thank-you note…', '/api/followup', { job_id: id, kind: 'thank_you', details: details.value })));
  }
  return box;
}

function prepBlock(id, application) {
  const detail = node('details', undefined, 'prep');
  const interview = application.interview;
  detail.open = !!interview && !interview.finished;
  detail.append(node('summary', interview ? `Interview prep · ${interview.stage}` : 'Interview prep & mock interview'));
  const stage = select(state.pipeline.stages, interview?.stage || 'First interview', 'Interview stage');
  const people = input('text', interview?.interviewers, 'Interviewer names and roles (optional)', 'Interviewers');
  const language = select(LANGUAGES, interview?.language || application.submitted?.language || 'English', 'Prep language');
  detail.append(el('div', 'row', stage, people, language, button(interview ? 'Rebuild prep pack ↗' : 'Build prep pack ↗', () =>
    act('Codex is researching the company and building your prep pack…', '/api/interview-prep',
      { job_id: id, stage: stage.value, interviewers: people.value, language: language.value }))));
  if (!interview) return detail;
  const p = interview.prep;
  detail.append(node('p', p.summary));
  if (p.likely_questions.length) {
    const list = node('ol');
    p.likely_questions.forEach(q => list.append(el('li', '', node('strong', q.question), node('span', ' ' + q.why, 'small'))));
    detail.append(node('h5', 'LIKELY QUESTIONS'), list);
  }
  if (p.star.length) {
    detail.append(node('h5', 'YOUR STAR EXAMPLES'));
    p.star.forEach(s => {
      const d = node('details', undefined, 'star');
      d.append(node('summary', s.question),
        el('dl', '', node('dt', 'Situation'), node('dd', s.situation), node('dt', 'Task'), node('dd', s.task),
          node('dt', 'Action'), node('dd', s.action), node('dt', 'Result'), node('dd', s.result)), evidence([s.source]));
      detail.append(d);
    });
  }
  if (p.consistency.length) detail.append(node('h5', 'WHAT THEY HAVE READ ABOUT YOU'), bullets(p.consistency));
  if (p.tough_questions.length) {
    const list = node('ul');
    p.tough_questions.forEach(q => list.append(el('li', '', node('strong', q.question), node('p', q.answer, 'small'))));
    detail.append(node('h5', 'TOUGH QUESTIONS & HONEST BRIDGES'), list);
  }
  if (p.questions_to_ask.length) detail.append(node('h5', 'QUESTIONS TO ASK'), bullets(p.questions_to_ask));
  if (p.company_points.length) {
    const list = node('ul');
    p.company_points.forEach(c => list.append(el('li', '', document.createTextNode(c.point + ' '), link(c.url, 'source ↗'))));
    detail.append(node('h5', 'VERIFIED COMPANY & INTERVIEWER FACTS'), list);
  }
  if (p.logistics.length) detail.append(node('h5', 'LOGISTICS'), bullets(p.logistics));
  const mock = el('div', 'mock', node('h5', 'MOCK INTERVIEW'));
  interview.mock.forEach(m => {
    mock.append(el('div', 'message ' + (m.role === 'candidate' ? 'user' : 'assistant'), node('strong', m.role === 'candidate' ? 'YOU' : 'INTERVIEWER'), document.createTextNode(m.text)));
    if (m.feedback) mock.append(el('div', 'coach', node('strong', 'COACHING '), document.createTextNode(m.feedback)));
  });
  if (interview.finished) {
    mock.append(el('div', 'coach', node('strong', 'DEBRIEF '), document.createTextNode(interview.debrief)),
      button('Practise again', () => act('Restarting the mock interview…', '/api/mock', { job_id: id, restart: true })));
  } else if (!interview.mock.length) {
    mock.append(button('Start mock interview ↗', () => act('The interviewer is preparing…', '/api/mock', { job_id: id })));
  } else {
    const answer = node('textarea'); answer.rows = 4; answer.disabled = busy;
    answer.placeholder = 'Your answer… (write “stop” to finish and get a debrief)';
    answer.setAttribute('aria-label', 'Your answer');
    mock.append(answer, el('div', 'row', button('Send answer ↗', () => act('The interviewer is listening…', '/api/mock', { job_id: id, message: answer.value })),
      button('Restart', () => act('Restarting the mock interview…', '/api/mock', { job_id: id, restart: true }), 'text-button')));
  }
  detail.append(mock);
  return detail;
}

function applicationCard(id, application) {
  const timing = state.pipeline.timing[id];
  const job = application.job;
  const card = node('details', undefined, 'pipeline-card' + (timing.due ? ' due' : ''));
  const badges = el('span', 'badges', chip(state.pipeline.statuses[application.status], 'status-' + application.status),
    application.status !== 'drafted' && !FINAL.includes(application.status) && chip(`Quiet ${timing.quiet} day${timing.quiet === 1 ? '' : 's'}`),
    timing.due && chip('Follow-up due', 'urgent'), application.interview && !FINAL.includes(application.status) && chip('Interview prep'),
    job.overall != null && chip(`Fit ${job.overall}/100`));
  card.append(el('summary', 'card-summary', el('span', 'summary-main', node('strong', job.title),
    node('span', ` · ${job.company}${job.location ? ' · ' + job.location : ''}`, 'small')), badges));
  remembered(card, `app:${id}`, timing.due || (!!application.interview && !application.interview.finished));
  if (job.url) card.append(el('p', 'small', link(job.url, 'Open listing ↗')));
  const status = select(statusOptions(), application.status, 'New status');
  const date = input('date', todayISO(), '', 'Date');
  const note = input('text', '', 'Note, e.g. “Recruiter call booked for Tuesday”', 'Note');
  card.append(el('div', 'row update-row', status, date, note,
    button('Update', () => {
      if (status.value === application.status && note.value.trim()) act('Adding your note…', '/api/note', { job_id: id, note: note.value, date: date.value });
      else act('Updating your pipeline…', '/api/track', { job_id: id, status: status.value, date: date.value, note: note.value });
    })));
  const timeline = node('ol', undefined, 'timeline');
  application.events.slice().reverse().forEach(e => timeline.append(el('li', '', node('span', e.date, 'small'),
    node('strong', ` ${e.kind === 'status' ? state.pipeline.statuses[e.status] : e.kind === 'note' ? 'Note' : 'Message'}`),
    e.note ? document.createTextNode(` · ${e.note}`) : null)));
  const history = node('details'); history.append(node('summary', `Timeline (${application.events.length})`), timeline);
  card.append(history, followupBlock(id, application, timing), prepBlock(id, application));
  if (application.submitted) {
    const sent = node('details');
    sent.append(node('summary', 'What you sent'), node('h5', 'CV'), node('pre', application.submitted.cv), node('h5', 'COVER LETTER'), node('pre', application.submitted.cover_letter));
    card.append(sent);
  }
  card.append(button('Remove from pipeline', () => {
    if (confirm('Remove this application and its timeline, follow-ups and interview prep from your pipeline?')) act('Removing…', '/api/untrack', { job_id: id });
  }, 'text-button danger'));
  return card;
}

function renderPipeline() {
  const applications = Object.entries(state?.applications || {});
  $('#pipeline-empty').hidden = !!applications.length;
  $('#pipeline-content').hidden = !applications.length;
  if (!applications.length) return;
  renderStats();
  const filter = $('#status-filter').value;
  const shown = applications.filter(([id, a]) => filter === 'all' || (filter === 'due' ? state.pipeline.timing[id].due : !FINAL.includes(a.status)))
    .sort(([, a], [, b]) => lastActivity(b).localeCompare(lastActivity(a)));
  $('#applications-list').replaceChildren(...(shown.length ? shown.map(([id, a]) => applicationCard(id, a))
    : [node('div', 'No applications in this view.', 'empty')]));
}

// Growth & enrichment ---------------------------------------------------------
function renderGrowth() {
  const result = state?.upskill;
  $('#upskill-results').replaceChildren();
  if (!result) return;
  const box = $('#upskill-results');
  box.append(node('p', `${result.summary} (${result.jobs_analysed} jobs analysed on ${result.created})`, 'small'));
  const table = node('table', undefined, 'gap-table');
  table.append(el('tr', '', node('th', 'Skill'), node('th', 'Status'), node('th', 'Jobs needing it'), node('th', 'Priority')));
  const top = Math.max(result.jobs_analysed, 1);
  result.gaps.forEach(g => {
    const bar = el('div', 'bar heat-' + g.priority, node('span'));
    bar.firstChild.style.width = `${Math.round(100 * g.jobs / top)}%`;
    table.append(el('tr', '', el('td', '', node('strong', g.skill), g.new && chip('New'), node('p', g.why, 'small')),
      node('td', g.status), el('td', 'bar-cell', bar, node('span', ` ${g.jobs}`, 'small')), el('td', '', chip(g.priority, 'sev-' + g.priority))));
  });
  box.append(el('div', 'table-scroll', table));
  if (result.closed?.length) box.append(node('p', `No longer a gap since your last analysis: ${result.closed.join(', ')}`, 'notice quiet'));
  const themes = {};
  result.plan.forEach(p => (themes[p.theme] ||= []).push(p));
  for (const [theme, items] of Object.entries(themes)) {
    box.append(node('h5', theme.toUpperCase()));
    const grid = node('div', undefined, 'role-grid');
    items.forEach(p => {
      const resources = node('ul', undefined, 'source-list');
      p.resources.forEach(r => resources.append(el('li', '', link(r.url, r.title))));
      grid.append(el('article', 'role-card', node('span', `about ${p.hours} hours`, 'seniority'), node('h4', p.skill), bullets(p.steps), resources));
    });
    box.append(grid);
  }
  if (result.order.length) {
    const ol = node('ol'); result.order.forEach(o => ol.append(node('li', o)));
    box.append(node('h5', 'SUGGESTED STUDY ORDER'), ol);
  }
}

function renderExpand() {
  const links = state?.profile_links || [];
  $('#enrich-links').textContent = links.length ? `Links found in your CV and discussion: ${links.join(', ')}` : 'No public profile links were found in your CV. Add them below.';
  const box = $('#expand-results');
  box.replaceChildren();
  const result = state?.expand;
  if (!result) return;
  box.append(node('p', result.summary, 'small'));
  const boxes = [];
  result.findings.forEach((f, index) => {
    const check = document.createElement('input'); check.type = 'checkbox'; check.checked = true; check.disabled = busy;
    boxes.push([index, check]);
    box.append(el('label', 'finding', check, el('span', '', node('strong', f.competency), node('span', ` · ${f.category} · ${f.evidence} `, 'small'), link(f.url, 'source ↗'))));
  });
  if (result.skipped.length) box.append(bullets(result.skipped.map(s => `Skipped ${s.url}: ${s.reason}`), 'small'));
  if (result.findings.length) {
    box.append(button('Add selected to my profile ↗', () => act('Codex is updating your profile with confirmed competencies…', '/api/expand-confirm',
      { indices: boxes.filter(([, c]) => c.checked).map(([i]) => i) })));
  } else box.append(node('p', 'No new competencies were found on these pages.', 'small'));
}

function renderAppearance() {
  $('#document-appearance').hidden = !state;
  if (appearanceProfile !== state?.id) {
    appearanceProfile = state?.id || null;
    portraitImage = null;
    appearanceDirty = false;
    $('#photo-file').value = '';
    $('#appearance-status').textContent = '';
  }
  const options = state?.document_settings || { photo_cv: true, photo_letter: false };
  if (!appearanceDirty) {
    $('#photo-on-cv').checked = options.photo_cv;
    $('#photo-on-letter').checked = options.photo_letter;
  }
  $('#photo-placeholder').hidden = !!(portraitImage || state?.photo);
  $('#saved-photo').hidden = !state?.photo || !!portraitImage;
  $('#photo-canvas').hidden = !portraitImage;
  $('#crop-controls').hidden = !portraitImage;
  $('#remove-photo').hidden = !state?.photo;
  if (state?.photo && !portraitImage) {
    $('#saved-photo').src = `/api/photo?id=${encodeURIComponent(state.id)}&v=${encodeURIComponent(state.photo.updated_at)}`;
  }
}

function drawPortrait() {
  if (!portraitImage) return;
  const canvas = $('#photo-canvas');
  const context = canvas.getContext('2d');
  const crop = photoCropRect(portraitImage.naturalWidth, portraitImage.naturalHeight,
    $('#photo-zoom').value, $('#photo-x').value, $('#photo-y').value);
  context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(portraitImage, crop.x, crop.y, crop.width, crop.height, 0, 0, canvas.width, canvas.height);
  $('#appearance-status').textContent = 'Unsaved picture changes';
}


// Profile enrichment: documents, career, style ----------------------------------
const CAREER_HINTS = {
  goals: 'Where do you want to be in 2-5 years? Which roles or responsibilities are you aiming for?',
  energizing: 'Tasks that give you energy, e.g. investigations, building processes, advising clients…',
  draining: 'Tasks you would rather avoid, e.g. pure maintenance, heavy travel, cold sales…',
  environment: 'Team size, autonomy, pace, leadership style, company culture you thrive in…',
  behavioral: 'Results of PI, DISC, Myers-Briggs, StrengthsFinder or your own self-assessment…',
  strengths: 'What colleagues and managers rely on you for…',
  growth: 'Areas you are actively developing…',
  dealbreakers: 'Anything that rules a job out for you (beyond location and language)…',
};

function renderDocuments() {
  const docs = state.documents;
  const labels = state.labels.doc_types;
  $('#documents-summary').textContent = docs.length ? `· ${docs.length} added` : '· none yet';
  $('#document-list').replaceChildren(...docs.map(d => el('li', '', el('span', '', node('strong', d.filename),
    node('span', ` · ${labels[d.type]} · ${d.characters.toLocaleString('en-CH')} characters · added ${d.added}`, 'small')),
    button('Remove', () => {
      if (confirm(`Remove “${d.filename}”? Pyxida refreshes your profile without it.`)) act('Removing the document and refreshing your profile…', '/api/documents', { action: 'remove', doc_id: d.id });
    }, 'text-button danger'))));
  const box = $('#reconcile');
  box.replaceChildren();
  const r = state.reconcile;
  if (!r) return;
  const open = r.conflicts.filter((_, i) => !r.resolved.includes(i)).length;
  box.append(node('h5', `CONSISTENCY CHECK · ${open ? `${open} conflict${open === 1 ? '' : 's'} to resolve` : 'no open conflicts'}`), node('p', r.summary, 'small'));
  r.conflicts.forEach((c, index) => {
    const resolved = r.resolved.includes(index);
    const card = el('div', 'conflict' + (resolved ? ' resolved' : ''),
      el('div', 'badges', chip(c.severity === 'high' ? 'An employer could notice' : 'Minor', c.severity === 'high' ? 'danger-chip' : 'flag-chip'), resolved && chip('Resolved', 'ok-chip')),
      node('strong', c.topic), node('p', `CV: ${c.cv_says}`, 'small'), node('p', `${c.document}: ${c.document_says}`, 'small'));
    if (!resolved) {
      const custom = input('text', '', 'Or write the correct version', 'Correct information');
      const resolve = (correct, value) => act('Recording your correction and refreshing your profile…', '/api/resolve-conflict', { index, correct, value });
      card.append(el('div', 'row', button('The CV is right', () => resolve('cv')), button('The document is right', () => resolve('document')),
        custom, button('Save correction', () => resolve('custom', custom.value), 'text-button')));
    }
    box.append(card);
  });
  if (r.additions.length) {
    box.append(node('h5', 'IN YOUR DOCUMENTS BUT NOT IN YOUR CV'), bullets(r.additions.map(a => `${a.fact} (${a.document})`)),
      node('p', 'Pyxida can already cite these. Consider adding the important ones to your CV.', 'small'));
  }
}

function renderCareer() {
  const fields = state.labels.career_fields;
  const filled = Object.values(state.career || {}).filter(Boolean).length;
  $('#career-summary').textContent = filled ? `· ${filled} of ${Object.keys(fields).length} answered` : '· not started';
  const snapshot = state.id + JSON.stringify(state.career || {});
  if (snapshot === careerRendered) return;
  careerRendered = snapshot;
  const form = $('#career-form');
  form.replaceChildren();
  for (const [key, label] of Object.entries(fields)) {
    const area = node('textarea'); area.rows = 2; area.name = key; area.maxLength = 2000;
    area.value = (state.career || {})[key] || ''; area.placeholder = CAREER_HINTS[key] || '';
    form.append(labeled(label, area));
  }
  const save = node('button', 'Save career profile', 'secondary'); save.type = 'submit';
  form.append(el('div', 'form-footer', node('p', 'Saved without using Codex. Applies to your next search, evaluation and drafts.', 'small'), save));
}

function renderStyle() {
  const box = $('#style-content');
  box.replaceChildren();
  const style = state.style;
  const letters = state.documents.filter(d => d.type === 'application').length;
  $('#style-summary').textContent = style ? `· learned from ${style.sources.length} document${style.sources.length === 1 ? '' : 's'}` : letters ? '· ready to learn' : '· add a past application first';
  if (style) {
    box.append(node('p', style.summary), node('h5', 'TONE'), node('p', style.tone, 'small'), node('h5', 'STRUCTURE'), node('p', style.structure, 'small'));
    if (style.habits.length) box.append(node('h5', 'KEEP'), bullets(style.habits));
    if (style.avoid.length) box.append(node('h5', 'AVOID'), bullets(style.avoid));
    if (style.openings.length) box.append(node('h5', 'OPENING PATTERNS'), bullets(style.openings));
    box.append(node('p', `Learned ${style.created} from ${style.sources.join(', ')}.`, 'small'));
  }
  const actions = el('div', 'row');
  if (letters) actions.append(button(style ? 'Learn again ↗' : 'Learn my writing style ↗', () => act('Codex is reading your past applications…', '/api/style', { action: 'learn' })));
  else actions.append(node('p', 'Add a past application or cover letter under Supporting documents first.', 'small'));
  if (style) actions.append(button('Forget my style', () => act('Removing your writing style…', '/api/style', { action: 'clear' }), 'text-button danger'));
  box.append(actions);
}

function renderCalibration() {
  const box = $('#calibration-content');
  box.replaceChildren();
  const c = state.calibration;
  const minimum = state.labels.calibration_minimum;
  $('#calibration-summary').textContent = c ? `· ${c.confidence} confidence from ${c.sample_size} outcomes` : `· needs ${minimum} resolved applications`;
  box.append(node('p', 'Once applications resolve (an interview, a rejection, or no response), Pyxida can learn which kinds of roles get traction for you and feed that into future fit scores.', 'small'));
  if (c) {
    box.append(el('p', '', chip(`${c.confidence} confidence`, c.confidence === 'low' ? 'flag-chip' : 'ok-chip'), document.createTextNode(` ${c.summary}`)));
    if (c.worked.length) box.append(node('h5', 'WHAT GOT TRACTION'), bullets(c.worked));
    if (c.did_not.length) box.append(node('h5', 'WHAT DID NOT'), bullets(c.did_not));
    if (c.adjustments.length) box.append(node('h5', 'APPLIED TO FUTURE SCORING'), bullets(c.adjustments));
    box.append(node('p', `Calibrated ${c.created}.`, 'small'));
  }
  box.append(el('div', 'row', button(c ? 'Recalibrate ↗' : 'Calibrate from my outcomes ↗', () => act('Codex is looking for patterns in your outcomes…', '/api/calibrate', {})),
    c && button('Stop using calibration', () => act('Removing calibration…', '/api/calibrate', { clear: true }), 'text-button danger')));
}

function renderCoverage() {
  const coverage = state?.shortlist?.portal_coverage || [];
  $('#coverage-container').hidden = !coverage.length;
  $('#coverage').replaceChildren(...coverage.map(c => el('li', c.sources ? '' : 'quiet-portal', node('span', c.name),
    node('span', c.sources ? `${c.sources} source${c.sources === 1 ? '' : 's'} · ${c.jobs} on shortlist` : 'no results', 'small health'))));
  const enabled = (state?.portals || []).filter(p => p.enabled).length;
  $('#portal-note').textContent = state ? `· ${enabled} portal${enabled === 1 ? '' : 's'} enabled` : '';
}

function renderNav() {
  const counts = { jobs: state?.shortlist?.jobs.length, applications: state && Object.keys(state.drafts).length,
    pipeline: state?.pipeline.stats.followups_due };
  for (const [key, value] of Object.entries(counts)) {
    $(`#nav-${key}`).hidden = !value;
    $(`#nav-${key}`).textContent = value || '';
  }
}

// Codex connection ------------------------------------------------------------
function renderCodex(status) {
  const box = $('#codex-status');
  box.replaceChildren(el('p', '', chip(status.configured ? 'Connected' : 'Not connected', status.configured ? 'ok-chip' : 'flag-chip'),
    document.createTextNode(` ${status.message}`)), node('p', `Model: ${status.model}`, 'small'));
  const actions = el('div', 'row');
  if (status.configured) {
    actions.append(button('Sign out of Codex', () => {
      if (confirm('Sign this Pyxida instance out of your ChatGPT account?')) run('Signing out of Codex…', async () => { await api('/api/codex/logout', {}); await refreshProfiles(); });
    }, 'text-button danger'));
  } else {
    actions.append(button('Sign in with ChatGPT ↗', () => run('Starting the Codex sign-in…', async () => renderLogin(await api('/api/codex/login', {}))), 'primary'));
  }
  box.append(actions);
}

function renderLogin(login) {
  clearTimeout(loginTimer);
  const box = $('#codex-login');
  box.replaceChildren();
  if (login.status === 'waiting' || login.status === 'starting') {
    const steps = el('ol', 'login-steps',
      el('li', '', document.createTextNode('Open '), login.url ? link(login.url, login.url) : node('span', 'the sign-in page'), document.createTextNode(' on any device and sign in to ChatGPT.')),
      el('li', '', document.createTextNode('Enter this one-time code: '), node('code', login.code || '…', 'device-code'), login.code && copyButton(() => login.code)),
      node('li', 'Come back here. Pyxida connects automatically once you approve.'));
    box.append(el('div', 'login-box', node('h3', 'Approve this sign-in'), steps,
      node('p', 'Only enter this code on the official OpenAI page you opened yourself. It expires after 15 minutes.', 'small'),
      button('Cancel', async () => renderLogin(await api('/api/codex/cancel', {})), 'text-button')));
    loginTimer = setTimeout(pollLogin, 3000);
  } else if (login.message && login.status !== 'idle') {
    box.append(node('p', login.message, 'notice' + (login.status === 'success' ? ' quiet' : ' error')));
  }
}

async function pollLogin() {
  try {
    const login = await api('/api/codex/login');
    renderLogin(login);
    if (login.status === 'success') await refreshProfiles();
  } catch { loginTimer = setTimeout(pollLogin, 5000); }
}

function render() {
  $('#welcome').hidden = !!state;
  $('#profile-content').hidden = !state;
  $('#jobs-empty').hidden = !!state;
  $('#shortlist').hidden = !state;
  $('#results-heading').hidden = !state?.shortlist;
  $('#sources-container').hidden = !state?.shortlist?.sources?.length;
  $('#stale').hidden = !state?.shortlist || !selectedJobs().some(jobStale);
  const selected = state?.selected.length || 0;
  $('#selection-bar').hidden = !selected;
  $('#selection-text').textContent = `${selected} job${selected === 1 ? '' : 's'} selected.`;
  $('#selected-count').replaceChildren(...(selected
    ? [document.createTextNode(`${selected} job${selected === 1 ? '' : 's'} selected for your next move.`)]
    : [document.createTextNode('Select jobs in '), el('a', '', document.createTextNode('Jobs')), document.createTextNode(' to begin.')]));
  const jobsLink = $('#selected-count a'); if (jobsLink) jobsLink.href = '#/jobs';
  if (state) {
    $('#profile-summary').textContent = state.profile.summary;
    $('#strengths').replaceChildren(...state.profile.strengths.map(s => node('span', s)));
    $('#languages').textContent = `Languages: ${state.profile.languages.join(' · ') || 'Not yet established'}`;
    $('#roles').replaceChildren();
    state.profile.roles.forEach(role => {
      const card = node('article', undefined, 'role-card');
      const detail = node('details');
      detail.append(node('summary', 'See the evidence'), evidence(role.evidence));
      card.append(node('span', role.seniority, 'seniority'), node('h4', role.title), node('p', role.reason), detail);
      $('#roles').append(card);
    });
    $('#messages').replaceChildren();
    state.discussion.forEach(m => {
      const message = node('div', undefined, 'message ' + m.role);
      message.append(node('strong', m.role === 'user' ? 'YOU' : 'PYXIDA'), document.createTextNode(m.text));
      $('#messages').append(message);
    });
    $('#questions').replaceChildren();
    if (state.profile.questions.length) $('#questions').append(bullets(state.profile.questions));
    $('#questions').className = 'questions';
    const p = state.preferences;
    $('#cities').value = p.cities; $('#work-style').value = p.work_style;
    $('#salary').value = p.salary; $('#constraints').value = p.constraints; $('#focus').value = p.focus;
    $('#permit').value = p.permit || ''; $('#depth').value = p.depth || 'standard';
    if (state.shortlist) {
      $('#results-count').textContent = `${state.shortlist.jobs.length} opportunities to explore`;
      $('#search-summary').textContent = state.shortlist.summary;
      $('#sources').replaceChildren();
      (state.shortlist.sources || []).forEach(s => { const li = node('li'); li.append(link(s.url, s.title)); $('#sources').append(li); });
    }
    renderDocuments(); renderCareer(); renderStyle(); renderCalibration(); renderPortals();
  } else {
    $('#cities').value = ''; $('#work-style').value = ''; $('#salary').value = ''; $('#constraints').value = ''; $('#permit').value = '';
    $('#depth').value = 'standard';
    $('#focus').value = 'AML, KYC, financial crime, finance and banking; include relevant adjacent roles';
    careerRendered = null;
  }
  $('#portals').hidden = !state;
  renderCoverage(); renderNav();
  renderJobs(); renderDrafts(); renderAppearance(); renderExpand(); renderPipeline(); renderGrowth(); controls();
}

async function refreshProfiles() {
  const status = await api('/api/status');
  $('#connection').textContent = status.configured ? 'Codex connected' : 'Connect Codex';
  $('#connection').classList.toggle('ready', status.configured);
  $('#setup').hidden = status.configured;
  $('#setup').replaceChildren(document.createTextNode('Pyxida needs a Codex connection. '), el('a', '', document.createTextNode('Sign in with ChatGPT in Settings')), document.createTextNode('.'));
  $('#setup a').href = '#/settings';
  $('#access-settings').hidden = !status.auth;
  renderCodex(status);
  if (status.codex_login === 'waiting' || status.codex_login === 'starting') pollLogin();
  $('#profile-picker').replaceChildren(node('option', 'Start a new profile'));
  $('#profile-picker').firstChild.value = '';
  status.profiles.forEach(p => {
    const option = node('option', p.filename); option.value = p.id; $('#profile-picker').append(option);
  });
  if (state) $('#profile-picker').value = state.id;
}

$('#cv-file').addEventListener('change', e => {
  selectedFile = e.target.files[0] || null;
  $('#file-label').textContent = selectedFile ? selectedFile.name : 'Choose your CV';
});

$('#intake-form').addEventListener('submit', e => {
  e.preventDefault();
  run('Codex is reading your experience and inferring suitable roles…', async () => {
    const data = { text: $('#cv-text').value, preferences: preferences() };
    if (selectedFile) {
      if (selectedFile.size > 8 * 1024 * 1024) throw new Error('Choose a CV smaller than 8 MB.');
      data.filename = selectedFile.name;
      data.file = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(new Error('Could not read this file.')); reader.readAsDataURL(selectedFile);
      });
    }
    saveState(await api('/api/intake', data));
    selectedFile = null; $('#cv-file').value = ''; $('#cv-text').value = ''; $('#file-label').textContent = 'Choose your CV';
    await refreshProfiles(); go('profile');
  });
});

$('#discussion-form').addEventListener('submit', e => {
  e.preventDefault();
  act('Codex is refining your profile…', '/api/discuss', { message: $('#message').value }, () => { $('#message').value = ''; });
});

$('#expand-form').addEventListener('submit', e => {
  e.preventDefault();
  if (!state) return;
  const urls = $('#expand-urls').value.split(/[\s,]+/).filter(Boolean);
  act('Codex is reading your public profiles…', '/api/expand', { urls }, () => { $('#expand-urls').value = ''; });
});

$('#evaluate-form').addEventListener('submit', e => {
  e.preventDefault();
  if (!state) return;
  act('Checking the posting and evaluating your fit… This can take a few minutes.', '/api/evaluate',
    { url: $('#evaluate-url').value.trim(), text: $('#evaluate-text').value, preferences: preferences() }, () => {
      $('#evaluate-url').value = ''; $('#evaluate-text').value = '';
      $('#results-heading').scrollIntoView({ behavior: 'smooth' });
    });
});

$('#portal-form').addEventListener('submit', e => {
  e.preventDefault();
  if (!state) return;
  act('Adding portal…', '/api/portals', { action: 'add', name: $('#portal-name').value, domain: $('#portal-domain').value }, () => {
    $('#portal-name').value = ''; $('#portal-domain').value = '';
  });
});

$('#search-form').addEventListener('submit', e => {
  e.preventDefault();
  if (!state) return;
  act('Codex is searching Swiss vacancies and comparing their requirements. This can take a few minutes…', '/api/search',
    { preferences: preferences() }, () => $('#results-heading').scrollIntoView({ behavior: 'smooth' }));
});

$('#prepare').addEventListener('click', () => run('Preparing your applications…', async () => {
  const ids = pendingApplications();
  const language = $('#draft-language').value;
  const review = $('#review-pass').checked;
  const failures = [];
  for (let i = 0; i < ids.length; i++) {
    const job = state.shortlist.jobs.find(j => j.id === ids[i]);
    setActivity(`Codex is checking the listing, drafting${review ? ', reviewing' : ''} and checking layout ${i + 1}/${ids.length}: ${job.company}…`);
    try { saveState(await api('/api/materials', { id: state.id, job_id: ids[i], language, review })); }
    catch (e) { failures.push(`${job.company}: ${e.message}`); }
  }
  if (failures.length) {
    const ready = state.selected.filter(id => {
      const draft = state.drafts[id];
      return draft && draft.revision === state.revision && draft.language === language;
    }).length;
    throw new Error(`Some drafts could not be prepared. ${ready ? `${ready} completed package${ready === 1 ? ' is' : 's are'} saved. Retry to prepare only the remaining jobs.` : 'No packages were prepared in this language. Retry the remaining jobs.'}\n${failures.join('\n')}`);
  }
}));

$('#upskill-button').addEventListener('click', () => act('Codex is mapping your skill gaps and finding learning resources…', '/api/upskill', {}));
$('#status-filter').addEventListener('change', renderPipeline);
$('#draft-language').addEventListener('change', controls);

$('#photo-file').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file || !state) return;
  run('Loading your applicant picture…', async () => {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('Choose a JPEG, PNG or WebP picture.');
    if (file.size > 5 * 1024 * 1024) throw new Error('Choose a picture smaller than 5 MB.');
    const dataURL = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('The picture could not be read.')); reader.readAsDataURL(file);
    });
    const image = new Image(); image.src = dataURL;
    try { await image.decode(); } catch { throw new Error('This picture could not be decoded. Try another JPEG or PNG.'); }
    if (image.naturalWidth * image.naturalHeight > 24000000) throw new Error('Choose a picture with at most 24 megapixels.');
    if (image.naturalWidth < 80 || image.naturalHeight < 100) throw new Error('Choose a larger picture so it stays clear in print.');
    portraitImage = image;
    appearanceDirty = true;
    $('#photo-zoom').value = '1'; $('#photo-x').value = '50'; $('#photo-y').value = '50';
    renderAppearance(); drawPortrait();
  });
});
for (const id of ['#photo-zoom', '#photo-x', '#photo-y']) $(id).addEventListener('input', drawPortrait);
for (const id of ['#photo-on-cv', '#photo-on-letter']) $(id).addEventListener('change', () => {
  appearanceDirty = true;
  $('#appearance-status').textContent = 'Unsaved appearance changes';
});
$('#save-appearance').addEventListener('click', () => run('Saving your document appearance…', async () => {
  const data = { id: state.id, settings: { photo_cv: $('#photo-on-cv').checked, photo_letter: $('#photo-on-letter').checked } };
  if (portraitImage) data.photo = $('#photo-canvas').toDataURL('image/jpeg', 0.94).split(',')[1];
  const next = await api('/api/document-settings', data);
  portraitImage = null; appearanceDirty = false; $('#photo-file').value = '';
  saveState(next);
  $('#appearance-status').textContent = 'Saved. Document previews updated.';
}));
$('#remove-photo').addEventListener('click', () => run('Removing the applicant picture…', async () => {
  const next = await api('/api/document-settings', { id: state.id, remove_photo: true });
  portraitImage = null; appearanceDirty = false; $('#photo-file').value = ''; saveState(next);
  $('#appearance-status').textContent = 'Picture removed from document layouts.';
}));

$('#fit-filter').addEventListener('change', renderJobs);
$('#profile-picker').addEventListener('change', e => run('Opening your profile…', async () => {
  if (e.target.value) await openProfile(e.target.value);
  else { state = null; store('remove'); render(); go('profile'); }
}));
$('#delete-profile').addEventListener('click', () => {
  if (!state || !confirm('Delete this local profile, its discussion, shortlist, application drafts and pipeline?')) return;
  run('Deleting this profile…', async () => {
    await api('/api/delete', { id: state.id });
    state = null; store('remove');
    render(); await refreshProfiles();
  });
});

$('#document-form').addEventListener('submit', e => {
  e.preventDefault();
  const file = $('#document-file').files[0];
  if (!state || !file) { error('Choose a document to upload.'); return; }
  run('Reading the document, checking it against your CV and refreshing your profile…', async () => {
    const encoded = await readFile(file, 8 * 1024 * 1024, 'document');
    saveState(await api('/api/documents', { id: state.id, action: 'add', type: $('#document-type').value, filename: file.name, file: encoded }));
    $('#document-file').value = '';
  });
});

$('#career-form').addEventListener('submit', e => {
  e.preventDefault();
  const career = Object.fromEntries([...$('#career-form').querySelectorAll('textarea')].map(t => [t.name, t.value]));
  careerRendered = null;
  act('Saving your career profile…', '/api/career', { career });
});

$('#sign-out').addEventListener('click', () => run('Signing out…', async () => {
  await api('/api/logout', {});
  location.href = '/login';
}));

async function openProfile(id) {
  const next = await api(`/api/profile?id=${encodeURIComponent(id)}`);
  $('#search-box').open = !next.shortlist;
  saveState(next);
  $('#profile-picker').value = id;
  if (next.running_task) {
    const resume = async () => {
      setActivity('Finishing an operation that was still running…');
      const result = await waitForTask(next.running_task);
      if (result?.id) saveState(result);
    };
    if (busy) await resume(); else await run('Finishing an operation that was still running…', resume);
  }
}

async function init() {
  showView();
  try {
    await refreshProfiles();
    const id = store('get');
    if (id) {
      try { await openProfile(id); } catch { store('remove'); }
    }
    render();
  } catch (e) { error(e.message); }
}
init();
