<?php if (!defined('SOFTACULOUS')) { http_response_code(403); exit; } ?>
<style><?php require __DIR__ . '/style.css'; ?></style>
<div class="mwu" id="mwu" data-token="<?= $escape($csrf) ?>" data-demo="<?= $demo ? '1' : '0' ?>">
  <h2>Mass WP Update</h2>
  <p>Update your WordPress sites from one place and manage the premium plugin ZIPs you use.</p>
  <div id="mwu-error" class="mwu-message mwu-error" role="alert" hidden></div>
  <div id="mwu-notice" class="mwu-message" role="status" hidden></div>
  <div class="mwu-tabs" role="tablist" aria-label="Mass WP Update">
    <button type="button" role="tab" id="mwu-tab-updates" aria-controls="mwu-updates" aria-selected="true">Updates</button>
    <button type="button" role="tab" id="mwu-tab-premium" aria-controls="mwu-premium" aria-selected="false" tabindex="-1">Premium plugins</button>
  </div>
  <section id="mwu-updates" role="tabpanel" aria-labelledby="mwu-tab-updates">
    <div class="mwu-options">
      <fieldset><legend>Updates</legend>
        <label><input type="checkbox" data-option="core" checked> Update WordPress core</label>
        <label><input type="checkbox" data-option="plugins" checked> Update plugins</label>
        <label><input type="checkbox" data-option="premium" checked> Update premium plugins</label>
        <label><input type="checkbox" data-option="themes"> Update themes</label>
      </fieldset>
      <fieldset><legend>Backups</legend>
        <label><input type="checkbox" data-option="backup_plugins" checked> Backup plugins</label>
        <label><input type="checkbox" data-option="backup_themes" checked> Backup themes</label>
        <label><input type="checkbox" data-option="backup_database" checked> Backup database</label>
        <label for="mwu-keep-backups">Keep latest backups per site
          <select id="mwu-keep-backups"><?php for ($keep = 1; $keep <= 10; $keep++) { ?><option value="<?= $keep ?>"><?= $keep ?></option><?php } ?></select>
        </label>
        <p class="mwu-help">Selected backups run before updates. If a backup fails, that site's updates stop.</p>
        <p class="mwu-help">Older backup sets are removed only after the new set finishes successfully.</p>
      </fieldset>
    </div>
    <div class="mwu-toolbar"><button type="button" id="mwu-scan">Refresh sites</button><span id="mwu-site-count"></span></div>
    <div id="mwu-discovery-errors"></div>
    <div class="mwu-scroll"><table><thead><tr><th><input type="checkbox" id="mwu-select-all" aria-label="Select all sites"></th><th>Site</th><th>Account</th><th>WordPress</th><th>PHP</th><th>Status</th><th>Backups</th></tr></thead><tbody id="mwu-sites"></tbody></table></div>
    <div class="mwu-toolbar"><button type="button" class="mwu-primary" id="mwu-update">Update selected sites</button><span id="mwu-selection"></span></div>
    <section class="mwu-job" id="mwu-job" hidden aria-live="polite">
      <h3>Activity</h3><p id="mwu-job-status"></p>
      <button type="button" id="mwu-stop" hidden>Stop after current site</button>
      <div id="mwu-results"></div>
    </section>
    <section class="mwu-job">
      <h3>Previous updates &amp; backups</h3>
      <div class="mwu-toolbar"><select id="mwu-history" aria-label="Previous update job"></select><button type="button" id="mwu-view-history">View results</button><button type="button" id="mwu-older" hidden>Load older jobs</button></div>
      <div id="mwu-history-results"></div>
    </section>
  </section>
  <section id="mwu-premium" role="tabpanel" aria-labelledby="mwu-tab-premium" hidden>
    <p>Select one or more installable plugin ZIPs. Each name and version is read from the plugin, so ZIP filenames do not matter.</p>
    <div class="mwu-toolbar"><button type="button" id="mwu-add">+ Add premium plugins</button><input type="file" id="mwu-add-file" accept=".zip,application/zip" multiple hidden><span id="mwu-upload-status" role="status"></span></div>
    <div id="mwu-upload-results" class="mwu-scroll" hidden aria-label="ZIP upload results"></div>
    <p class="mwu-help">ZIPs stay in a private server directory. Replacing a ZIP updates the library; sites change only when you run an update. Maximum ZIP: 256 MiB; expanded contents: 1 GiB.</p>
    <div class="mwu-scroll"><table><thead><tr><th>Plugin</th><th>Version</th><th>Replace ZIP</th><th>Sites</th></tr></thead><tbody id="mwu-packages"></tbody></table></div>
    <p class="mwu-help">Premium updates use the backup choices in the Updates tab and apply only to installed copies with an older version.</p>
  </section>
</div>
<script><?php require __DIR__ . '/app.js'; ?></script>
