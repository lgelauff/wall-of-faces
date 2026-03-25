/**
 * buffet.js — accept/reject interactions for the card builder buffet.
 *
 * All saves go to POST /api/save-profile.
 * Images are resolved lazily via GET /api/resolve-image?filename=&width=.
 *
 * State lives in memory as plain arrays; on each toggle the full updated
 * list is posted to /api/save-profile so the server stays authoritative.
 */

(function () {
  'use strict';

  const i18n = JSON.parse(document.getElementById('buffet-i18n').textContent);

  // ── Tab switching ─────────────────────────────────────────────────────────

  document.querySelectorAll('.buffet-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.buffet-tab').forEach(t => t.classList.remove('is-active'));
      document.querySelectorAll('.buffet-panel').forEach(p => p.classList.remove('is-active'));
      tab.classList.add('is-active');
      document.getElementById(tab.dataset.panel).classList.add('is-active');
    });
  });

  // "Next" buttons on the explanation panel
  document.querySelectorAll('[data-goto-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.gotoPanel;
      document.querySelectorAll('.buffet-tab').forEach(t => {
        t.classList.toggle('is-active', t.dataset.panel === target);
      });
      document.querySelectorAll('.buffet-panel').forEach(p => {
        p.classList.toggle('is-active', p.id === target);
      });
    });
  });

  // ── Image resolution ──────────────────────────────────────────────────────

  async function resolveImage(filename, width) {
    try {
      const resp = await fetch(
        `/api/resolve-image?filename=${encodeURIComponent(filename)}&width=${width}`
      );
      if (!resp.ok) return null;
      return (await resp.json()).url || null;
    } catch (_) {
      return null;
    }
  }

  // Lazily load all images that have a data-filename attribute and no real src
  async function loadImages() {
    for (const img of document.querySelectorAll('img[data-filename]')) {
      if (img.getAttribute('src')) continue; // already has an explicit src
      const url = await resolveImage(img.dataset.filename, img.classList.contains('avatar-option') ? 80 : 40);
      if (url) img.src = url;
    }
  }

  // ── API save ──────────────────────────────────────────────────────────────

  async function saveField(field, value) {
    try {
      await fetch('/api/save-profile', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ [field]: value }),
      });
    } catch (_) { /* silently ignore; card preview will reflect truth on reload */ }
  }

  // ── Avatar grid (profile picture) ────────────────────────────────────────

  document.querySelectorAll('.avatar-option:not(.content-image-option)').forEach(img => {
    img.addEventListener('click', () => {
      document.querySelectorAll('.avatar-option:not(.content-image-option)').forEach(i => i.classList.remove('is-selected'));
      img.classList.add('is-selected');
      saveField('avatar_filename', img.dataset.filename);
      // Keep manual input in sync
      const manual = document.getElementById('input-avatar-manual');
      if (manual) manual.value = img.dataset.filename;
    });
  });

  // ── Text field auto-save (on blur / change) ───────────────────────────────

  document.querySelectorAll('[data-field]').forEach(el => {
    el.addEventListener('change', () => saveField(el.dataset.field, el.value.trim() || null));
  });

  // ── Content images (multi-select, max 3) ─────────────────────────────────

  function getSelectedContentImages() {
    return Array.from(document.querySelectorAll('.content-image-option.is-selected'))
      .map(img => img.dataset.filename);
  }

  document.querySelectorAll('.content-image-option').forEach(img => {
    img.addEventListener('click', () => {
      const isSelected = img.classList.contains('is-selected');
      if (!isSelected && getSelectedContentImages().length >= 3) return; // max 3
      img.classList.toggle('is-selected');
      saveField('content_image_filenames', getSelectedContentImages());
    });
  });

  // ── Userboxes ─────────────────────────────────────────────────────────────

  function getAcceptedUserboxes() {
    return Array.from(document.querySelectorAll('.buffet-item.is-accepted[data-type="userbox"]'))
      .map(el => JSON.parse(el.dataset.payload));
  }

  document.querySelectorAll('[data-action="toggle-userbox"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = btn.closest('.buffet-item');
      const accepted = item.classList.toggle('is-accepted');
      btn.textContent = accepted ? i18n.reject : i18n.accept;
      btn.className = `btn btn--small ${accepted ? 'btn--secondary' : 'btn--primary'}`;
      saveField('userboxes', getAcceptedUserboxes());
    });
  });

  // ── Barnstars ─────────────────────────────────────────────────────────────

  function getAcceptedBarnstars() {
    return Array.from(document.querySelectorAll('.buffet-item.is-accepted[data-type="barnstar"]'))
      .map(el => JSON.parse(el.dataset.payload));
  }

  document.querySelectorAll('[data-action="toggle-barnstar"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = btn.closest('.buffet-item');
      const accepted = item.classList.toggle('is-accepted');
      btn.textContent = accepted ? i18n.reject : i18n.accept;
      btn.className = `btn btn--small ${accepted ? 'btn--secondary' : 'btn--primary'}`;
      saveField('barnstars', getAcceptedBarnstars());
    });
  });

  // ── Badges ────────────────────────────────────────────────────────────────

  function getSelectedBadgeIds() {
    return Array.from(document.querySelectorAll('.buffet-item.is-accepted[data-type="badge"]'))
      .map(el => el.dataset.badgeId);
  }

  document.querySelectorAll('[data-action="toggle-badge"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = btn.closest('.buffet-item');
      const accepted = item.classList.toggle('is-accepted');
      btn.textContent = accepted ? i18n.reject : i18n.accept;
      btn.className = `btn btn--small ${accepted ? 'btn--secondary' : 'btn--primary'}`;
      saveField('selected_achievements', getSelectedBadgeIds());
    });
  });

  // ── Proud of ──────────────────────────────────────────────────────────────

  function getAcceptedProudOf() {
    return Array.from(document.querySelectorAll('.buffet-item.is-accepted[data-type="proud-of"]'))
      .map(el => JSON.parse(el.dataset.payload));
  }

  document.querySelectorAll('[data-action="toggle-proud-of"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = btn.closest('.buffet-item');
      const accepted = item.classList.toggle('is-accepted');
      btn.textContent = accepted ? i18n.reject : i18n.accept;
      btn.className = `btn btn--small ${accepted ? 'btn--secondary' : 'btn--primary'}`;
      saveField('proud_of', getAcceptedProudOf());
    });
  });

  // Manual proud-of addition
  const inputProudOfTitle = document.getElementById('input-proud-of-title');
  const inputProudOfWiki  = document.getElementById('input-proud-of-wiki');
  const btnAddProudOf     = document.getElementById('btn-add-proud-of');

  if (btnAddProudOf) {
    btnAddProudOf.addEventListener('click', () => {
      const title = inputProudOfTitle.value.trim();
      if (!title) return;
      const wiki = inputProudOfWiki ? (inputProudOfWiki.value.trim() || null) : null;

      // Build a synthetic accepted item and append it to the list
      const payload = { title, type: 'other', wiki };
      const list    = document.getElementById('proud-of-list') ||
                      (() => {
                        const el = document.createElement('div');
                        el.id = 'proud-of-list';
                        inputProudOfTitle.closest('.buffet-section').prepend(el);
                        return el;
                      })();

      const row = document.createElement('div');
      row.className = 'buffet-item is-accepted';
      row.dataset.type    = 'proud-of';
      row.dataset.payload = JSON.stringify(payload);
      const displayLabel = wiki && wiki !== (inputProudOfWiki.dataset.homeWiki || '') ? `${wiki}:${title}` : title;
      row.innerHTML = `
        <div class="buffet-item__body">
          <div class="buffet-item__label">${displayLabel}</div>
          <div class="buffet-item__meta">${wiki || ''}</div>
        </div>
        <div class="buffet-item__actions">
          <button class="btn btn--small btn--secondary" data-action="toggle-proud-of">
            ${i18n.reject}
          </button>
        </div>
      `;
      row.querySelector('[data-action="toggle-proud-of"]').addEventListener('click', btn => {
        const item = btn.target.closest('.buffet-item');
        const accepted = item.classList.toggle('is-accepted');
        btn.target.textContent = accepted ? i18n.reject : i18n.accept;
        btn.target.className = `btn btn--small ${accepted ? 'btn--secondary' : 'btn--primary'}`;
        saveField('proud_of', getAcceptedProudOf());
      });

      list.appendChild(row);
      inputProudOfTitle.value = '';
      saveField('proud_of', getAcceptedProudOf());
    });
  }

  // ── Extra sections (inline form — no prompt()) ────────────────────────────

  function getExtraSections() {
    return Array.from(document.querySelectorAll('.buffet-item[data-type="extra-section"]'))
      .map(el => ({
        title: el.dataset.sectionTitle || '',
        text:  el.dataset.sectionText  || '',
      }));
  }

  function makeSectionRow(title, text) {
    const row = document.createElement('div');
    row.className = 'buffet-item';
    row.dataset.type         = 'extra-section';
    row.dataset.sectionTitle = title;
    row.dataset.sectionText  = text;
    row.innerHTML = `
      <div class="buffet-item__body">
        <div class="buffet-item__label">${title}</div>
        <div class="buffet-item__meta">${text.substring(0, 80)}${text.length > 80 ? '…' : ''}</div>
      </div>
      <div class="buffet-item__actions">
        <button class="btn btn--small btn--secondary" data-action="edit-section">${i18n.edit}</button>
        <button class="btn btn--small btn--secondary" data-action="remove-section">${i18n.reject}</button>
      </div>
    `;
    row.querySelector('[data-action="remove-section"]').addEventListener('click', () => {
      row.remove();
      saveField('extra_sections', getExtraSections());
    });
    row.querySelector('[data-action="edit-section"]').addEventListener('click', () => {
      inputSectionTitle.value = row.dataset.sectionTitle;
      inputSectionText.value  = row.dataset.sectionText;
      btnAddSection.dataset.editingRow = '';
      btnAddSection._editTarget = row;
      btnAddSection.textContent = i18n.save_section;
      inputSectionTitle.focus();
    });
    return row;
  }

  document.querySelectorAll('[data-action="remove-section"]').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('.buffet-item').remove();
      saveField('extra_sections', getExtraSections());
    });
  });

  document.querySelectorAll('[data-action="edit-section"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('.buffet-item');
      inputSectionTitle.value = row.dataset.sectionTitle;
      inputSectionText.value  = row.dataset.sectionText;
      btnAddSection._editTarget = row;
      btnAddSection.textContent = i18n.save_section;
      inputSectionTitle.focus();
    });
  });

  const btnAddSection     = document.getElementById('btn-add-section');
  const inputSectionTitle = document.getElementById('input-section-title');
  const inputSectionText  = document.getElementById('input-section-text');

  if (btnAddSection) {
    btnAddSection.addEventListener('click', () => {
      const title = inputSectionTitle.value.trim().substring(0, 100);
      const text  = inputSectionText.value.trim().substring(0, 500);
      if (!title || !text) return;

      const list = document.getElementById('extra-sections-list');

      if (btnAddSection._editTarget) {
        // Update existing row
        const target = btnAddSection._editTarget;
        target.dataset.sectionTitle = title;
        target.dataset.sectionText  = text;
        target.querySelector('.buffet-item__label').textContent = title;
        target.querySelector('.buffet-item__meta').textContent  =
          text.substring(0, 80) + (text.length > 80 ? '…' : '');
        btnAddSection._editTarget = null;
        btnAddSection.textContent = i18n.add_section;
      } else {
        list.appendChild(makeSectionRow(title, text));
      }

      inputSectionTitle.value = '';
      inputSectionText.value  = '';
      saveField('extra_sections', getExtraSections());
    });
  }

  // ── Gather-progress polling ───────────────────────────────────────────────
  // If gather is still running when the buffet page loads, poll every 3 s.
  // Reload at key progress milestones so new data sections appear incrementally.
  // Final reload happens when gather reaches 'done' or 'error'.
  //
  // Milestones match when data is flushed to DB in gather.py:
  //   70 — avatars + barnstars written
  //   85 — LLM userboxes + proud-of written
  //   95 — badges written
  //  100 — gather complete

  const elBanner = document.getElementById('gathering-banner');
  if (elBanner) {
    let gatherPollTimer = null;
    const RELOAD_MILESTONES = [70, 85, 95];
    let lastReloadProgress = parseInt(elBanner.dataset.progress || '0', 10);

    function updateBanner(pct) {
      const fill = elBanner.querySelector('.gathering-banner__fill');
      if (fill) fill.style.width = pct + '%';
    }

    async function pollGatherStatus() {
      try {
        const resp = await fetch('/api/gather-status');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.status === 'done' || data.status === 'error') {
          clearInterval(gatherPollTimer);
          window.location.reload();
          return;
        }
        const pct = data.progress || 0;
        updateBanner(pct);
        // Reload when we cross a milestone for the first time
        const crossed = RELOAD_MILESTONES.find(m => m > lastReloadProgress && pct >= m);
        if (crossed !== undefined) {
          lastReloadProgress = pct;
          window.location.reload();
        }
      } catch (_) { /* network error — keep polling */ }
    }

    gatherPollTimer = setInterval(pollGatherStatus, 3000);
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  loadImages();
})();
