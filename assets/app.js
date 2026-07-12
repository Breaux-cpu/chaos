// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

const connIndicator = document.querySelector('#conn-indicator');
const statusDot = document.querySelector('#status-dot');
const statusLabel = document.querySelector('#status-label');
const statusDetail = document.querySelector('#status-detail');
const scanList = document.querySelector('#scan-list');
const emptyState = document.querySelector('#empty-state');

const MATCH_DISPLAY_MS = 1500;
const ALERT_DISPLAY_MS = 3000;
let revertTimer = null;

const pentestError = document.querySelector('#pentest-error');
const jobList = document.querySelector('#job-list');
const jobEmptyState = document.querySelector('#job-empty-state');
const jobsById = new Map();

const ui = new WebUI();
ui.on_connect(onConnected);
ui.on_disconnect(onDisconnected);
ui.on_message('scan_history', ({ scans }) => renderScans(scans));
ui.on_message('scan', onScan);
ui.on_message('error', onError);
ui.on_message('job_update', onJobUpdate);
ui.on_message('pentest_run_response', onPentestRunResponse);

document.querySelectorAll('[data-tool]').forEach((btn) => {
  btn.addEventListener('click', () => runTool(btn.dataset.tool));
});

function onConnected() {
  connIndicator.textContent = 'connected';
  connIndicator.className = 'mono connected';
  setStatus('scanning', 'Scanning', 'Watching the camera for a code');
}

function onDisconnected() {
  connIndicator.textContent = 'disconnected';
  connIndicator.className = 'mono disconnected';
  setStatus('', 'Board unreachable', '—');
}

function onScan(entry) {
  prependScan(entry);
  setStatus('match', 'Match found', `${entry.type} · ${truncate(entry.content, 40)}`);
  scheduleRevert(MATCH_DISPLAY_MS);
}

function onError({ message }) {
  setStatus('alert', 'Camera trouble', message || 'Unknown error');
  scheduleRevert(ALERT_DISPLAY_MS);
}

function scheduleRevert(delayMs) {
  clearTimeout(revertTimer);
  revertTimer = setTimeout(() => {
    setStatus('scanning', 'Scanning', 'Watching the camera for a code');
  }, delayMs);
}

function setStatus(state, label, detail) {
  statusDot.className = `status-dot ${state}`;
  statusLabel.textContent = label;
  statusDetail.textContent = detail;
}

function renderScans(scans) {
  scanList.innerHTML = '';
  (scans || []).forEach(prependScan);
  emptyState.style.display = scans && scans.length ? 'none' : 'block';
}

function prependScan(entry) {
  emptyState.style.display = 'none';
  const li = document.createElement('li');
  li.innerHTML = `
    <span class="scan-type mono">${escapeHtml(entry.type)}</span>
    <span class="scan-content mono">${escapeHtml(entry.content)}</span>
    <span class="scan-time">${new Date(entry.timestamp * 1000).toLocaleTimeString()}</span>
  `;
  scanList.prepend(li);
}

function truncate(str, max) {
  return str.length > max ? `${str.slice(0, max)}…` : str;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// --- Pentest toolkit ---

function runTool(tool) {
  const target = document.querySelector('#pt-target').value.trim();
  const payload = { tool, target };

  if (tool === 'nmap') {
    payload.profile = document.querySelector('#nmap-profile').value;
  } else if (tool === 'hydra') {
    payload.service = document.querySelector('#hydra-service').value;
  } else if (tool === 'tcpdump') {
    payload.interface = document.querySelector('#tcpdump-iface').value;
    payload.filter = document.querySelector('#tcpdump-filter').value;
    payload.duration = 15;
  }

  pentestError.style.display = 'none';
  ui.send_message('pentest_run', payload);
}

function onPentestRunResponse(response) {
  if (response && response.error) {
    pentestError.textContent = response.error;
    pentestError.style.display = 'block';
  }
}

function onJobUpdate(job) {
  jobEmptyState.style.display = 'none';
  let li = jobsById.get(job.id);
  if (!li) {
    li = document.createElement('li');
    jobsById.set(job.id, li);
    jobList.prepend(li);
  }
  li.innerHTML = `
    <div class="job-head">
      <span class="job-tool mono">${escapeHtml(job.tool)}</span>
      <span class="job-target mono">${escapeHtml(job.target)}</span>
      <span class="job-badge job-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
    </div>
    <pre class="job-output mono">${escapeHtml(job.output || '')}</pre>
  `;
}
