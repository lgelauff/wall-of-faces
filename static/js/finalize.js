/**
 * finalize.js — card preview page: overflow detection and finalize flow.
 *
 * Overflow detection: polls the card iframe height (or the card element height)
 * against the A5 page height to warn before finalizing.
 *
 * Finalize flow:
 *   1. POST /api/finalize
 *   2. If 409 snapshot_limit_reached → show confirm-prune dialog
 *   3. If confirmed → POST /api/finalize with confirm_prune=true and keep_version
 *   4. On success → update finalize-notice
 */

(function () {
  'use strict';

  const i18n     = JSON.parse(document.getElementById('finalize-i18n').textContent);
  const meta     = JSON.parse(document.getElementById('finalize-meta').textContent);

  const elBtnFinalize    = document.getElementById('btn-finalize');
  const elOverflowWarn   = document.getElementById('overflow-warning');
  const elFinalizeNotice = document.getElementById('finalize-notice');
  const elPruneDialog    = document.getElementById('prune-dialog');
  const elPruneDialogBody= document.getElementById('prune-dialog-body');
  const elBtnPruneOk     = document.getElementById('btn-prune-ok');
  const elBtnPruneCancel = document.getElementById('btn-prune-cancel');

  // ── Overflow detection ────────────────────────────────────────────────────
  // A5 at 96 dpi ≈ 793px tall. Use the card element height as a proxy.

  const A5_HEIGHT_PX = 793;

  function checkOverflow() {
    const card = document.querySelector('.card');
    if (!card) return;
    // Compare actual content height against the card's visible height rather
    // than a hardcoded constant — works regardless of browser zoom or CSS changes.
    const overflows = card.scrollHeight > card.clientHeight;
    elOverflowWarn.classList.toggle('is-visible', overflows);
  }

  // Run once and on resize
  checkOverflow();
  window.addEventListener('resize', checkOverflow);

  // ── Finalize ──────────────────────────────────────────────────────────────

  async function doFinalize(confirmPrune, keepVersion) {
    elBtnFinalize.disabled = true;

    try {
      const body = {};
      if (confirmPrune) {
        body.confirm_prune = true;
        body.keep_version  = keepVersion;
      }

      const resp = await fetch('/api/finalize', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });

      const data = await resp.json();

      if (resp.status === 422 && data.error === 'card_overflow') {
        alert(i18n.overflow_error);
        return;
      }

      if (resp.status === 429) {
        const secs = data.retry_after_seconds || '?';
        alert(i18n.too_soon.replace('{seconds}', secs));
        return;
      }

      if (resp.status === 409 && data.error === 'snapshot_limit_reached') {
        // Show confirm-prune dialog
        elPruneDialogBody.textContent = data.detail || '';
        elPruneDialog.classList.add('is-visible');
        // Store keep_version for the confirm button
        elBtnPruneOk.dataset.keepVersion = data.keep_version || '';
        return;
      }

      if (resp.ok) {
        // Success — update notice without page reload
        const d = new Date(data.snapshot_at);
        const formatted = d.toLocaleDateString('nl-NL', { day: '2-digit', month: '2-digit', year: 'numeric' });
        elFinalizeNotice.textContent = `Je kaart is gefinaliseerd op ${formatted}.`;
      }

    } catch (_) {
      alert('Netwerkfout — probeer opnieuw.');
    } finally {
      elBtnFinalize.disabled = false;
    }
  }

  elBtnFinalize.addEventListener('click', () => {
    doFinalize(false, null);
  });

  // ── Prune dialog ──────────────────────────────────────────────────────────

  elBtnPruneOk.addEventListener('click', () => {
    elPruneDialog.classList.remove('is-visible');
    doFinalize(true, elBtnPruneOk.dataset.keepVersion);
  });

  elBtnPruneCancel.addEventListener('click', () => {
    elPruneDialog.classList.remove('is-visible');
  });

  // Close dialog on backdrop click
  elPruneDialog.addEventListener('click', e => {
    if (e.target === elPruneDialog) elPruneDialog.classList.remove('is-visible');
  });
})();
