# Mass WP Update for Webuzo

Update WordPress sites from the Webuzo administrator panel and keep your premium plugin ZIPs in one private library.

## Updates

1. Click **Refresh sites** to find WordPress installations in active hosting accounts.
2. Select the sites and the actions: **WordPress core**, **plugins**, **premium plugins**, and **themes**.
3. Choose whether to back up **plugins**, **themes**, and the **database**.
4. Click **Update selected sites**.

Sites run one at a time. Selected backups finish before that site's updates begin; a failed backup stops updates for that site. Activity shows completed actions, skipped premium updates, errors, and backup downloads. **Previous updates & backups** keeps earlier results and downloads available. **Stop after current site** lets the current site finish before the next one starts.

Core updates include the WordPress database update, across the network for Multisite. Each row represents one WordPress installation; updates to a Multisite installation affect its shared files and network. Regular plugin updates exclude plugins registered in the premium library. Premium updates use your uploaded packages, apply only to matching installed plugins with an older version, and check their declared WordPress, PHP, and plugin dependencies. Plugins are not activated automatically.

Backups contain the selected `wp-content/plugins` and `wp-content/themes` directories and/or a SQL database export. They are not a complete site backup or an automatic rollback. They stay in `/var/lib/mass-wp-update-webuzo/backups`, accessible only to root and the authenticated administrator's download actions. Earlier backups remain on the server when another job starts.

## Premium plugins

Open **Premium plugins** and click **+ Add premium plugins**. Select one or more installable ZIPs supplied by their developers. Files upload one at a time, with progress and a result for each ZIP; a failed file does not stop the rest. Existing library entries are kept until you use **Replace ZIP**. The plugin reads each name and version from its PHP header without executing it, and stores it as `plugin-directory-version.zip` in a root-private directory outside website document roots.

**Replace ZIP** replaces the library's current package for that plugin. It checks the plugin directory and main file against the existing entry. **View sites** shows installed copies and their versions; select the older copies and click **Update selected**. This action uses the backup choices in the Updates tab and updates only that premium plugin.

Uploading or replacing a ZIP never updates a site automatically. Running jobs keep the exact package versions selected when they started. ZIP filenames do not need to include a version. Additional premium plugins can be added at any time.

Upload an individual installable plugin ZIP rather than a vendor download bundle. ZIPs are checked for unsafe paths, links, duplicate entries, encryption, and integrity. Resource limits are 256 MiB per ZIP, 1 GiB of expanded contents, and 100,000 entries. Licensed packages belong in the private library, not in this public repository.

## Installation

Requires Webuzo's Linux administrator panel, Python 3.9+, a working native Webuzo PHP installation, and official WP-CLI installed at `/usr/local/bin/wp`, `/usr/bin/wp`, or `/usr/local/lib/wp-cli/wp-cli.phar`. The WP-CLI file and its parent directories must be owned by root and must not be writable by hosting users. See the [official WP-CLI installation instructions](https://make.wordpress.org/cli/handbook/guides/installing/).

WordPress must use the standard content, plugin, and theme directories. `wp-config.php` may be in the installation directory or its parent within the same hosting account. Installations with custom content directories are reported as unsupported before an update or backup starts.

1. Download the plugin ZIP from **Releases**.
2. Upload it through **Webuzo Admin → Plugins** and activate it.
3. Open **Mass WP Update**, then click **Refresh sites**.
4. Add premium ZIPs and choose sites and actions in the two tabs.

Account and domain discovery uses Webuzo's existing CLI. Each WordPress process runs with that hosting account's Unix UID and the domain's native Webuzo PHP runtime. Suspended accounts are skipped. Account ownership or unsupported WordPress layouts produce an error instead of falling back to root. WordPress commands, database exports, and file backups never execute as root.

System paths must be owned by UID 0 and must not be writable by other users. Group-writable paths are accepted only for GID 0 when all explicit and primary-group members have UID 0. MySQL/MariaDB client discovery runs only for database exports, so it does not block site discovery or unrelated actions. Rejected paths report the exact component, ownership, permissions, and reason.

The plugin creates its own private data directory, a separate working directory for account processes, and a native-panel request directory under `/run`. A systemd tmpfiles entry recreates that request directory after reboot. No WordPress companion plugin, scheduled update, daemon, sudoers entry, or change to Webuzo core files is required. Deactivation stops new jobs and preserves the library and backups; wait for a running job to finish before deactivating.

Commands have timeouts, captured output is limited to 4 MiB, and backup output reserves 256 MiB of available filesystem space. A command failure is recorded and stops the remaining actions for that site. Updates already completed on that site remain applied.

## Development

Run backend tests as root on a disposable Linux Webuzo test installation; they use isolated temporary data and the native `webuzo` Unix identity.

```sh
python3 -m unittest discover -s tests -v
php tests/test_panel.php
python3 tools/build.py
```

The builder includes only the plugin's source, license, and documentation. Tests use synthetic ZIPs; premium packages, credentials, backups, and deployment data are excluded.

Author: [Byron Iniotakis](https://www.iniotakis.com). Licensed under GPL-3.0-or-later. See [UPSTREAM](UPSTREAM) for attribution.

This project is an independent third-party integration. It is not affiliated with, endorsed, sponsored, or supported by Webuzo or Softaculous. The Webuzo name is used only to describe compatibility with the hosting platform.
