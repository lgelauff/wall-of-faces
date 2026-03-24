/**
 * admin.js — admin page: snapshot and mark-processed actions.
 */

(function () {
  'use strict';

  const elStatus = document.getElementById('admin-status');

  function setStatus(msg) {
    if (elStatus) elStatus.textContent = msg;
  }

  async function post(url, body) {
    const resp = await fetch(url, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    return resp.json();
  }

  // Snapshot all (no username → batch)
  const btnAll = document.getElementById('btn-snapshot-all');
  if (btnAll) {
    btnAll.addEventListener('click', async () => {
      btnAll.disabled = true;
      try {
        const data = await post('/admin/snapshot', {});
        setStatus(`Snapshot aangevraagd voor ${data.queued?.length ?? 0} gebruiker(s).`);
      } catch (_) {
        setStatus('Fout bij aanvragen snapshot.');
      } finally {
        btnAll.disabled = false;
      }
    });
  }

  // Per-row snapshot and mark-processed buttons
  document.querySelectorAll('[data-action="snapshot-user"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const username = btn.dataset.username;
      btn.disabled = true;
      try {
        await post('/admin/snapshot', { username });
        setStatus(`Snapshot aangevraagd voor ${username}.`);
      } catch (_) {
        setStatus('Fout.');
      } finally {
        btn.disabled = false;
      }
    });
  });

  document.querySelectorAll('[data-action="mark-processed"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const username = btn.dataset.username;
      btn.disabled = true;
      try {
        await post('/admin/mark-processed', { username });
        setStatus(`${username} gemarkeerd als verwerkt.`);
        // Reload page so the timestamp updates
        setTimeout(() => window.location.reload(), 1000);
      } catch (_) {
        setStatus('Fout.');
      }
    });
  });
})();
