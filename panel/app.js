(() => {
  'use strict';
  const root = document.getElementById('mwu');
  if (!root) return;
  document.title = 'Mass WP Update';
  const byId = id => document.getElementById('mwu-' + id);
  const options = ['core','plugins','premium','themes','backup_plugins','backup_themes','backup_database'];
  let snapshot = null, job = null, timer = null, uploading = false, resultsSignature = '';
  let historyCursor = null, settingsLoaded = false, deleting = false, settingsQueue = Promise.resolve();
  const selected = new Set();
  const demo = root.dataset.demo === '1';
  root.querySelectorAll('[data-option], #mwu-keep-backups').forEach(input => { input.disabled = true; });
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function siteLink(site) {
    const link = element('a', site.domain, 'mwu-site-link');
    let url;
    try { url = new URL(site.url || 'https://' + site.domain); }
    catch (error) { return element('span', site.domain); }
    if (!['http:','https:'].includes(url.protocol) || url.username || url.password) return element('span', site.domain);
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
  }
  function message(text, error = false) {
    const target = byId(error ? 'error' : 'notice');
    target.textContent = text || ''; target.hidden = !text;
    if (error) byId('notice').hidden = true;
  }
  function errorView(item) {
    const raw = item.error || '', summary = item.error_summary || raw.split('\n')[0];
    const box = element('div', undefined, 'mwu-error-info');
    box.append(element('div', summary, 'mwu-failure'));
    if (raw !== summary || raw.includes('\n')) {
      const details = element('details', undefined, 'mwu-error-details');
      details.append(element('summary', 'Technical details'), element('pre', raw));
      box.append(details);
    }
    return box;
  }
  async function request(payload, chunk, keepalive = false) {
    const body = new FormData();
    body.append('token', root.dataset.token); body.append('payload', JSON.stringify(payload));
    if (chunk) body.append('chunk', chunk, 'chunk.bin');
    const response = await fetch(window.location.href, {method:'POST',credentials:'same-origin',headers:{'X-MWU-Request':'1'},body,cache:'no-store',keepalive});
    let result;
    try { result = await response.json(); }
    catch (error) { throw new Error('The panel returned an unexpected response. Reload the page and check the job status before retrying.'); }
    if (!response.ok || !result.ok) throw new Error(result.error || 'Request failed.');
    return result;
  }
  function active() { return job && ['queued','running'].includes(job.state); }
  function updateControls() {
    byId('scan').disabled = demo || active() || deleting;
    byId('update').disabled = demo || active() || deleting || selected.size === 0 || !!(snapshot && snapshot.prerequisites);
    byId('selection').textContent = selected.size + ' site(s) selected';
    root.querySelectorAll('[data-premium-update]').forEach(button => {
      const row = button.closest('.mwu-package-sites');
      button.disabled = demo || active() || deleting || !row.querySelector('input:checked') || !!(snapshot && snapshot.prerequisites);
    });
    root.querySelectorAll('[data-upload-button]').forEach(button => { button.disabled = demo || uploading || deleting; });
    byId('add').disabled = demo || uploading || deleting;
    root.querySelectorAll('[data-delete-backups]').forEach(button => { button.disabled = demo || active() || deleting; });
    root.querySelectorAll('[data-delete-premium]').forEach(button => { button.disabled = demo || uploading || deleting || (active() && job.kind === 'update'); });
  }
  function choices(premiumOnly = false) {
    const result = {};
    for (const key of options) result[key] = root.querySelector('[data-option="' + key + '"]').checked;
    if (premiumOnly) { result.core = false; result.plugins = false; result.themes = false; result.premium = true; }
    return result;
  }
  function saveSettings() {
    if (demo || !settingsLoaded) return;
    const payload = {action:'save_settings',options:choices(),keep_backups:Number(byId('keep-backups').value)};
    settingsQueue = settingsQueue.then(() => request(payload, null, true)).catch(error => {
      message('Could not save preferences: ' + error.message, true);
    });
  }
  async function run(ids, premium = null) {
    byId('error').hidden = true;
    try {
      await settingsQueue;
      const result = await request({action:'update',sites:ids,options:choices(premium !== null),premium,keep_backups:Number(byId('keep-backups').value)});
      setTab('updates'); drawJob(result.job); schedule();
    } catch (error) { message(error.message, true); }
  }
  function renderSites() {
    const body = byId('sites'); body.replaceChildren();
    const sites = Object.values(snapshot.inventory.sites);
    const valid = new Set(sites.filter(site => !site.error).map(site => site.id));
    for (const id of selected) if (!valid.has(id)) selected.delete(id);
    for (const site of sites) {
      const row = element('tr'), cell = element('td'), check = element('input');
      row.dataset.siteRow = site.id;
      check.type = 'checkbox'; check.checked = selected.has(site.id); check.disabled = !!site.error;
      check.setAttribute('aria-label', 'Select ' + site.domain);
      check.addEventListener('change', () => { if (check.checked) selected.add(site.id); else selected.delete(site.id); syncSelection(); });
      cell.append(check); row.append(cell);
      const name = element('td'), title = element('strong'); title.append(siteLink(site));
      name.append(title, element('div', site.path, 'mwu-path')); row.append(name);
      row.append(element('td',site.owner),element('td',site.core||'—'),element('td',site.php||'—'));
      const status=element('td');status.append(site.error?errorView(site):element('span',site.multisite?'Ready · Multisite':'Ready'));row.append(status);
      const backups = element('td'), remove = element('button', 'Delete backups'); remove.type = 'button'; remove.dataset.deleteBackups = site.id;
      remove.setAttribute('aria-label', 'Delete backups for ' + site.domain);
      remove.addEventListener('click', () => deleteBackups(site)); backups.append(remove); row.append(backups);
      body.append(row);
    }
    renderSiteResults();
    if (!sites.length) { const row = element('tr'), cell = element('td', 'No sites listed yet. Click Refresh sites to discover WordPress installations.'); cell.colSpan = 7; row.append(cell); body.append(row); }
    byId('site-count').textContent = sites.length + ' WordPress site(s)' + (snapshot.inventory.scanned_at ? ' · Last scan: ' + new Date(snapshot.inventory.scanned_at * 1000).toLocaleString() : '');
    const errors = byId('discovery-errors'); errors.replaceChildren();
    for(const item of snapshot.inventory.errors||[]){const box=element('div');box.append(element('strong',item.owner),errorView(item));errors.append(box);}
    syncSelection();
  }
  async function deleteBackups(site) {
    if (active() || deleting || !window.confirm('Delete all Mass WP Update backup sets for ' + site.domain + ' (' + site.path + ')?')) return;
    deleting = true; updateControls(); byId('error').hidden = true;
    try {
      const result = await request({action:'delete_backups',site:site.id});
      message(result.removed + ' backup set(s) deleted for ' + site.domain + '.' + (result.warnings.length ? ' Some backups could not be removed: ' + result.warnings.join('; ') : ''));
      byId('history-results').replaceChildren(); await load();
    } catch (error) { message(error.message, true); }
    finally { deleting = false; updateControls(); }
  }
  function syncSelection() {
    const checks = [...byId('sites').querySelectorAll('input:not(:disabled)')];
    // Keep row checkboxes and the master checkbox in sync after a bulk selection.
    const sites = Object.values(snapshot.inventory.sites);
    byId('sites').querySelectorAll('input').forEach((check, index) => { check.checked = selected.has(sites[index].id); });
    const master = byId('select-all'); master.checked = checks.length > 0 && selected.size === checks.length;
    master.indeterminate = selected.size > 0 && selected.size < checks.length;
    updateControls();
  }
  async function deletePremium(item) {
    if (uploading || deleting || (active() && job.kind === 'update') || !window.confirm('Delete ' + item.name + ' and its uploaded ZIP versions from the premium library? Installed plugins on your sites will remain unchanged.')) return;
    deleting = true; updateControls(); byId('error').hidden = true;
    try {
      const result = await request({action:'delete_premium',slug:item.slug});
      message(item.name + ' removed from the premium library.' + (result.warnings.length ? ' Some ZIP files could not be removed: ' + result.warnings.join('; ') : ''));
      await load();
    } catch (error) { message(error.message, true); }
    finally { deleting = false; updateControls(); }
  }
  function uploadInput(replace) {
    const input = element('input'); input.type = 'file'; input.accept = '.zip,application/zip'; input.hidden = true;
    input.addEventListener('change', async () => {
      if (input.files.length) await uploadFiles([input.files[0]], replace);
      input.value = '';
    });
    return input;
  }
  function renderPackages() {
    const body = byId('packages'); body.replaceChildren();
    const packages = [...snapshot.packages].sort((a, b) => Number(!!b.pending) - Number(!!a.pending));
    for (const item of packages) {
      const row = element('tr'), name = element('td');
      name.append(element('strong', item.name), element('div', item.plugin_file, 'mwu-path'));
      if (item.author) name.append(element('div', item.author, 'mwu-path'));
      row.append(name, element('td', item.pending ? 'Awaiting premium ZIP' : item.version, item.pending ? 'mwu-waiting' : ''));
      const replaceCell = element('td'), input = uploadInput(item.slug), replace = element('button', item.pending ? 'Upload ZIP' : 'Replace ZIP');
      replace.type = 'button'; replace.dataset.uploadButton = '1'; replace.addEventListener('click', () => input.click());
      replaceCell.append(replace, input); row.append(replaceCell);
      const sitesCell = element('td'), view = element('button', 'View sites (' + item.sites.length + ')');
      view.type = 'button'; view.setAttribute('aria-expanded','false'); sitesCell.append(view); row.append(sitesCell);
      const deleteCell = element('td'), remove = element('button', 'Delete'); remove.type = 'button'; remove.dataset.deletePremium = item.slug;
      remove.setAttribute('aria-label','Delete ' + item.name + ' from premium library');
      remove.addEventListener('click', () => deletePremium(item)); deleteCell.append(remove); row.append(deleteCell); body.append(row);
      const detailsRow = element('tr'); detailsRow.hidden = true;
      const details = element('td', undefined, 'mwu-package-sites'); details.colSpan = 5;
      details.append(element('h4', item.name + (item.pending ? ' · Awaiting premium ZIP' : ' · Available version ' + item.version)));
      if (item.pending) details.append(element('p', 'Upload the vendor’s installable ZIP to make this plugin available for updates.'));
      if (!item.sites.length) details.append(element('p', 'No installed copies found. Refresh sites after installing this plugin on a site.'));
      const eligible = [];
      for (const site of item.sites) {
        const label = element('label'), check = element('input'); check.type = 'checkbox'; check.value = site.id;
        check.disabled = !site.newer || !!site.error;
        if (!check.disabled) eligible.push(check);
        check.addEventListener('change', updateControls);
        const description = element('span'); description.append(siteLink(site), document.createTextNode(' · ' + site.owner + ' · Installed ' + site.version));
        label.append(check, description);
        if (site.error) label.append(errorView(site));
        else if (item.pending) label.append(element('span', 'Awaiting premium ZIP', 'mwu-badge'));
        else if (!site.newer) label.append(element('span', 'Current or newer', 'mwu-badge'));
        details.append(label);
      }
      if (eligible.length) {
        const select = element('button', 'Select sites with an older version'); select.type = 'button';
        select.addEventListener('click', () => { eligible.forEach(check => { check.checked = true; }); updateControls(); });
        details.append(select, document.createTextNode(' '));
      }
      if (!item.pending) {
        const update = element('button', 'Update selected'); update.type = 'button'; update.className = 'mwu-primary'; update.dataset.premiumUpdate = '1';
        update.addEventListener('click', () => run([...details.querySelectorAll('input:checked')].map(check => check.value), item.slug)); details.append(update);
      }
      detailsRow.append(details); body.append(detailsRow);
      view.addEventListener('click', () => { detailsRow.hidden = !detailsRow.hidden; view.setAttribute('aria-expanded', String(!detailsRow.hidden)); });
    }
    if (!snapshot.packages.length) { const row = element('tr'), cell = element('td', 'Your premium library is empty. Add your first plugin ZIP above.'); cell.colSpan = 5; row.append(cell); body.append(row); }
    updateControls();
  }
  async function uploadFile(file, replace, progress) {
    if (!file.name.toLowerCase().endsWith('.zip') || !file.size || file.size > 256 * 1024 * 1024) throw new Error('Select a non-empty plugin ZIP up to 256 MiB.');
    let identity = null;
    try {
      const initial = await request({action:'upload_begin',size:file.size,replace}); identity = initial.upload;
      for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
        progress('Uploading · ' + Math.floor(offset * 100 / file.size) + '%');
        await request({action:'upload_chunk',upload:identity,offset}, file.slice(offset, offset + 1024 * 1024));
      }
      progress('Checking ZIP…');
      const result = await request({action:'upload_finish',upload:identity}); identity = null;
      return result.package;
    } catch (error) {
      if (identity) { try { await request({action:'upload_abort',upload:identity}); } catch (unused) { /* A later refresh can still show a completed import. */ } }
      throw error;
    }
  }
  async function uploadFiles(selection, replace = null) {
    const files = Array.from(selection);
    if (uploading || !files.length) return;
    uploading = true; updateControls(); byId('error').hidden = true; byId('notice').hidden = true;
    const output = byId('upload-results'); output.hidden = false; output.replaceChildren();
    const table = element('table'), head = element('thead'), heading = element('tr'), body = element('tbody');
    heading.append(element('th','ZIP file'), element('th','Result')); head.append(heading); table.append(head,body); output.append(table);
    const rows = files.map(file => {
      const row = element('tr'), status = element('td','Waiting'); row.append(element('td',file.name),status); body.append(row); return status;
    });
    let imported = 0, failed = 0;
    try {
      for (let index = 0; index < files.length; index++) {
        const file = files[index], status = rows[index];
        const progress = text => {
          status.textContent = text;
          byId('upload-status').textContent = 'File ' + (index + 1) + ' of ' + files.length + ' · ' + file.name + ' · ' + text;
        };
        try {
          progress('Starting…');
          const item = await uploadFile(file, replace, progress);
          status.textContent = 'Imported: ' + item.name + ' ' + item.version;
          imported++;
        } catch (error) {
          status.textContent = error.message; status.className = 'mwu-failure'; failed++;
        }
      }
      const summary = imported + ' ZIP(s) imported' + (failed ? ' · ' + failed + ' failed. See each file below.' : '.');
      byId('upload-status').textContent = summary; message(summary);
      try { await load(); }
      catch (error) { message('Uploads finished, but the library could not refresh: ' + error.message, true); }
    } finally { uploading = false; updateControls(); }
  }
  function download(button, jobId, site, name) {
    const form = element('form'); form.method = 'POST'; form.action = window.location.href; form.target = '_blank'; form.hidden = true;
    const values = {token:root.dataset.token,mwu_download:'1',payload:JSON.stringify({action:'download_prepare',job:jobId,site,name})};
    for (const [key,value] of Object.entries(values)) { const input = element('input'); input.type = 'hidden'; input.name = key; input.value = value; form.append(input); }
    root.append(form); form.submit(); form.remove();
  }
  function drawJob(value) {
    job = value; byId('job').hidden = !job;
    const signature = JSON.stringify(job ? [job.id, job.results] : null);
    if (signature !== resultsSignature) {
      resultsSignature = signature; renderSiteResults();
    }
    if (!job) { updateControls(); return; }
    const siteProgress=byId('site-progress');
    siteProgress.hidden=job.kind!=='update'||!Number.isInteger(job.site_total)||job.site_total<1;
    siteProgress.textContent=siteProgress.hidden?'':'Website '+job.site_index+'/'+job.site_total;
    byId('job-status').textContent = job.message;
    byId('stop').hidden = !active();
    byId('stop').textContent = job.kind === 'scan' ? 'Stop scan' : 'Stop after current site';
    updateControls();
  }
  function renderSiteResults() {
    const body = byId('sites');
    body.querySelectorAll('.mwu-site-result').forEach(row => row.remove());
    const results = new Map(job && job.kind === 'update' ? job.results.map(item => [item.site, item]) : []);
    for (const row of body.querySelectorAll('[data-site-row]')) {
      const item = results.get(row.dataset.siteRow);
      row.classList.toggle('mwu-has-result', !!item);
      if (!item) continue;
      const detail = element('tr', undefined, 'mwu-site-result'), cell = element('td');
      detail.dataset.resultSite = row.dataset.siteRow;
      cell.colSpan = 7; cell.append(renderResult(item, job.id, false)); detail.append(cell);
      row.after(detail);
    }
  }
  function renderResults(displayedJob, target) {
    target.replaceChildren();
    for (const item of displayedJob.results) target.append(renderResult(item, displayedJob.id));
  }
  function renderResult(item, displayedJobId, title = true) {
    const box = element('div', undefined, 'mwu-result' + (item.state === 'failed' || item.error ? ' failed' : ''));
    if (title) box.append(element('strong', item.domain || item.owner));
    if (item.actions && item.actions.length) box.append(element('p', 'Completed: ' + item.actions.join(', ')));
    if (item.state === 'complete' && !item.actions.length) box.append(element('p', 'Completed. No update was needed.'));
    if (item.skipped && item.skipped.length) { const list = element('details'), title = element('summary', 'Premium plugin details'); list.append(title, element('p', item.skipped.join('\n'))); box.append(list); }
    if (item.error) box.append(errorView(item));
    if (item.pending_premium && item.pending_premium.length) box.append(element('p', 'Added to premium library · Awaiting premium ZIP: ' + item.pending_premium.join(', ')));
    if (item.backup_storage) box.append(element('p', item.backup_storage.directory, 'mwu-path'));
    for (const warning of item.warnings || []) box.append(element('p', warning));
    if (item.backup_removed) box.append(element('p', 'Backups removed by retention or an administrator.'));
    if (!item.backup_removed && item.backups && item.backups.length) {
      const links = element('div', undefined, 'mwu-downloads');
      for (const file of [...item.backups, {name:'manifest.json'}]) {
        const button = element('button', 'Download ' + file.name); button.type = 'button';
        button.addEventListener('click', () => download(button, displayedJobId, item.site, file.name)); links.append(button);
      }
      box.append(links);
    }
    return box;
  }
  async function history(append = false) {
    const result = await request({action:'history',before:append ? historyCursor : null});
    const select = byId('history'); const previous = select.value;
    if (!append) select.replaceChildren();
    for (const item of result.jobs) {
      const option = element('option', new Date(item.created * 1000).toLocaleString() + ' · ' + item.message);
      option.value = item.id; select.append(option);
    }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    byId('view-history').disabled = select.options.length === 0;
    historyCursor = result.next_cursor; byId('older').hidden = historyCursor === null;
  }
  function schedule() {
    clearTimeout(timer);
    if (!active()) return;
    timer = setTimeout(async () => {
      try {
        const previous = active(), result = await request({action:'job_status'}); drawJob(result.job);
        if (previous && !active()) await load();
        schedule();
      } catch (error) { message(error.message, true); timer = setTimeout(() => load().catch(e => message(e.message, true)), 5000); }
    }, 1500);
  }
  async function load() {
    snapshot = await request({action:'status'});
    if(!settingsLoaded){byId('keep-backups').value=String(snapshot.settings.keep_backups);for(const key of options)root.querySelector('[data-option="'+key+'"]').checked=snapshot.settings.options[key];settingsLoaded=true;root.querySelectorAll('[data-option], #mwu-keep-backups').forEach(input=>{input.disabled=demo;});}
    renderSites(); renderPackages(); drawJob(snapshot.job);
    if (snapshot.prerequisites) message(snapshot.prerequisites, true);
    await history();
    schedule();
  }
  function setTab(name) {
    for (const key of ['updates','premium']) {
      byId(key).hidden = key !== name;
      byId('tab-' + key).setAttribute('aria-selected', String(key === name));
      byId('tab-' + key).tabIndex = key === name ? 0 : -1;
    }
  }
  for (const name of ['updates','premium']) {
    const tab = byId('tab-' + name);
    tab.addEventListener('click', () => setTab(name));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault(); const next = event.key === 'Home' ? 'updates' : event.key === 'End' ? 'premium' : name === 'updates' ? 'premium' : 'updates';
      setTab(next); byId('tab-' + next).focus();
    });
  }
  byId('scan').addEventListener('click', async () => {
    try { byId('error').hidden = true; const result = await request({action:'scan'}); drawJob(result.job); schedule(); }
    catch (error) { message(error.message, true); }
  });
  byId('select-all').addEventListener('change', event => {
    for (const site of Object.values(snapshot.inventory.sites)) if (!site.error) { if (event.target.checked) selected.add(site.id); else selected.delete(site.id); }
    syncSelection();
  });
  root.querySelectorAll('[data-option], #mwu-keep-backups').forEach(input=>input.addEventListener('change',saveSettings));
  byId('update').addEventListener('click', () => run([...selected]));
  byId('stop').addEventListener('click', async () => {
    try { const result = await request({action:'cancel',job:job.id}); message(result.message); }
    catch (error) { message(error.message, true); }
  });
  byId('add').addEventListener('click', () => byId('add-file').click());
  byId('view-history').addEventListener('click', async () => {
    try {
      const result = await request({action:'job_details',job:byId('history').value});
      renderResults(result.job, byId('history-results'));
    } catch (error) { message(error.message, true); }
  });
  byId('older').addEventListener('click', () => history(true).catch(error => message(error.message, true)));
  byId('add-file').addEventListener('change', async event => {
    if (event.target.files.length) await uploadFiles(event.target.files);
    event.target.value = '';
  });
  if (demo) message('Demo mode: updates and uploads are disabled.');
  else load().catch(error => message(error.message, true));
})();
