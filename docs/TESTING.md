# Verification

Verified on Ubuntu 24.04 with Webuzo 4.9.1 and real WordPress installations on two separate hosting accounts.

| Check | Result |
| --- | --- |
| Backend and shared-workflow regressions | 61 tests passed |
| Native panel authorization, forged actors, CSRF and upload rejection | 8 checks passed |
| Native ZIP upload, activation, site discovery and per-domain PHP | Passed |
| Existing premium detection, library replacement and scoped deletion | Regression suite passed |
| HTTP 403 without license/package evidence | No premium classification |
| Underscore, space and Unicode plugin main files | Accepted in real WordPress inventory |
| Plugin directory named `--all` | Real single-plugin WP-CLI update passed through positional API |
| Native document-root discovery | Subfolder found; old copy elsewhere in home excluded |
| Ownership, suspension, changed root and refreshed PHP runtime | Regression checks passed |
| Original command output larger than 4 MiB | Complete output retained |
| Per-site update/backup commands | No default addon time limit |
| Native addon replacement during an active job | Original worker/helper generation completed three downloadable ZIP backups |
| Deliberately failing second site | Failure retained and Website 2/2 included that site |
| Cleanup failure | Completed actions retained; warning recorded |
| Settings after refresh/new panel session | Server choices restored, including explicitly disabled themes |
| Premium-only job | General update/backup choices and retention preserved |
| Plugin/theme/database ZIP backup and download on both accounts | Integrity and recorded SHA256 verified |
| Restricted PHP CLI database export | Export succeeded; unrelated disabled process functions remained disabled |
| PHP/FPM and Webuzo core configuration | No edits required |

Database exports use account-owned WP-CLI processes. Webuzo's documented account database backups do not provide the individual SQL stream used by Hestia's `v-dump-database`; an account-wide backup is not substituted for a per-site export.

Run the automated suite as root on a disposable Linux Webuzo installation:

```sh
python3 -m unittest discover -s tests -v
/usr/local/emps/bin/php tests/test_panel.php
```

Runtime tests use disposable account-owned fixtures and private deployment data, which are excluded from the repository and release ZIP.

## 0.4.1 inline site results

Ported the result placement from Hestia commit `903dba7cf349`. The latest update results appear beneath their matching installation IDs; Activity contains only the job summary, website counter and current operation. Previous-job details retain their own download identities.

Browser checks rendered the actual Webuzo PHP view and JavaScript against fixture API replies. They covered two installations sharing one domain, a site without a result, successful and failed results, premium details, warnings, text escaping, exact job/site download requests, history, bulk selection, incremental polling, page reload and replacement by a discovery job. Desktop and narrow layouts were visually inspected; no addon JavaScript errors occurred. JavaScript and PHP syntax checks and all eight panel request rejection checks passed. No update or backup engine changes were made in this version; these checks did not run real site updates or deploy to a server.
