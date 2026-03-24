/**
 * gather.js — gather progress screen.
 *
 * On load: checks current gather status.
 * - idle, no consent given → shows LLM consent box
 * - idle, consent given    → shows start button
 * - queued / running       → shows progress bar, polls every 2 s
 * - done                   → redirects to /buffet
 * - error                  → shows error message
 */

(function () {
  'use strict';

  const i18n = JSON.parse(document.getElementById('gather-i18n').textContent);

  const elConsentBox   = document.getElementById('consent-box');
  const elStartBox     = document.getElementById('start-box');
  const elProgressBox  = document.getElementById('progress-box');
  const elProgressFill = document.getElementById('progress-fill');
  const elProgressLabel= document.getElementById('progress-label');
  const elStatusLine   = document.getElementById('status-line');

  const elDurationHint = document.getElementById('duration-hint');

  const elBtnConsentYes = document.getElementById('btn-consent-yes');
  const elBtnConsentNo  = document.getElementById('btn-consent-no');
  const elBtnStart      = document.getElementById('btn-start');

  let pollTimer = null;
  // Track whether consent has been explicitly set in this session (null = not yet asked)
  let consentKnown = false;

  function show(el)  { el.hidden = false; }
  function hide(el)  { el.hidden = true; }

  function setStatus(text) {
    elStatusLine.textContent = text;
  }

  function setProgress(pct) {
    elProgressFill.style.width = pct + '%';
  }

  // ── Render state ──────────────────────────────────────────────────────────

  function renderState(data) {
    const { status, progress, queue_position, error_message, avg_seconds } = data;

    hide(elConsentBox);
    hide(elStartBox);
    hide(elProgressBox);
    setStatus('');
    elDurationHint.textContent = '';

    if (status === 'idle' || status === 'error') {
      stopPolling();
      if (!consentKnown) {
        show(elConsentBox);
      } else {
        show(elStartBox);
      }
      if (status === 'error' && error_message) {
        setStatus(i18n.error.replace('{message}', error_message));
      }
    } else if (status === 'queued' || status === 'running') {
      show(elProgressBox);
      setProgress(progress || 0);
      elProgressLabel.textContent = i18n.progress;

      if (status === 'queued' && queue_position) {
        const msg = queue_position >= 5
          ? i18n.queue_position_many
          : i18n.queue_position.replace('{n}', queue_position);
        setStatus(msg);
      }

      if (avg_seconds) {
        const mins = Math.round(avg_seconds / 60 * 10) / 10;
        elDurationHint.textContent = i18n.expected_duration.replace('{min}', mins);
      }

      startPolling();
    } else if (status === 'done') {
      stopPolling();
      setStatus(i18n.done);
      setTimeout(() => { window.location.href = '/buffet'; }, 800);
    }
  }

  // ── API calls ─────────────────────────────────────────────────────────────

  async function fetchStatus() {
    try {
      const resp = await fetch('/api/gather-status');
      if (!resp.ok) return;
      renderState(await resp.json());
    } catch (_) { /* network error — keep polling */ }
  }

  async function startGather(llmConsent) {
    try {
      const resp = await fetch('/api/gather', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ llm_consent: llmConsent }),
      });
      const data = await resp.json();
      if (resp.status === 429) {
        setStatus('Wacht nog ' + (data.retry_after_seconds || '?') + ' seconden.');
        return;
      }
      consentKnown = true;
      renderState(data);
    } catch (_) {
      setStatus('Netwerkfout — probeer opnieuw.');
    }
  }

  // ── Polling ───────────────────────────────────────────────────────────────

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(fetchStatus, 2000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ── Event listeners ───────────────────────────────────────────────────────

  elBtnConsentYes.addEventListener('click', () => startGather(true));
  elBtnConsentNo.addEventListener('click',  () => startGather(false));
  elBtnStart.addEventListener('click',      () => startGather(null));

  // ── Init ──────────────────────────────────────────────────────────────────

  fetchStatus();
})();
