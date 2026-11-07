"""Native account metadata and strictly unprivileged WordPress processes."""
import hashlib
import functools
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import selectors
import resource
import signal
import stat
import subprocess
import time

import state

SOURCE = Path(__file__).resolve().parent
PANEL_PHP = Path('/usr/local/emps/bin/php')
CLI = Path('/usr/local/webuzo/cli.php')
ENV = {'PATH': '/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin', 'LANG': 'C.UTF-8'}


def native(kind):
    if kind not in ('users', 'domains'): raise ValueError('Invalid native discovery action')
    def request(*extra):
        result = subprocess.run([str(PANEL_PHP), str(CLI), '--api', 'act=' + kind, *extra],
            env=ENV, capture_output=True, text=True, timeout=120, check=True)
        data = json.loads(result.stdout)
        if data.get('error'): raise ValueError('Native account discovery failed')
        return data
    data = request()
    count = int(data['num_res'])
    rows = data[kind]
    if rows == [] and count == 0: return []
    if not isinstance(rows, dict): raise ValueError('Invalid native account listing')
    if len(rows) < count:
        data = request('--reslen=' + str(count)); rows = data[kind]
    if not isinstance(rows, dict) or len(rows) != count: raise ValueError('Incomplete native account listing')
    return list(rows.values())


def accounts():
    result = {}
    for row in native('users'):
        name = row['user']
        if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]*', name): raise ValueError('Invalid native account')
        unix = pwd.getpwnam(name)
        if unix.pw_uid == 0 or name == 'webuzo' or unix.pw_dir != row['homedir']:
            raise ValueError('Native account identity mismatch')
        if row['status']: continue
        result[name] = dict(owner=name, uid=unix.pw_uid, gid=unix.pw_gid, home=unix.pw_dir)
    return result


def runtime(version=''):
    if version:
        if not isinstance(version, str) or not re.fullmatch(r'(?:php)?[0-9]+(?:\.[0-9]+)?', version):
            raise ValueError('Unknown Webuzo PHP runtime')
        digits = version.replace('php', '').replace('.', '')
        path = Path('/usr/local/apps') / ('php' + digits) / 'bin/php'
    else:
        # Webuzo updates this system symlink when its default PHP changes.
        path = Path('/usr/local/bin/php').resolve(strict=True)
    if not re.fullmatch(r'/usr/local/apps/php[0-9]+/bin/php', str(path)):
        raise ValueError('Cannot resolve the native Webuzo PHP runtime')
    state.trusted(path)
    if not os.access(path, os.X_OK): raise ValueError('PHP runtime is not executable')
    return str(path)


def wp_cli():
    for candidate in ('/usr/local/bin/wp', '/usr/bin/wp', '/usr/local/lib/wp-cli/wp-cli.phar'):
        path = Path(candidate)
        if path.exists():
            state.trusted(path)
            if path.stat().st_mode & 0o022: raise ValueError('WP-CLI must be writable only by root')
            return str(path)
    raise ValueError('WP-CLI is required. Install its official PHAR at /usr/local/bin/wp, then refresh sites.')


def work(account):
    state.trusted(state.WORK, True)
    destination = state.WORK / secrets.token_hex(16)
    destination.mkdir(mode=0o700)
    os.chown(destination, account['uid'], account['gid'])
    return destination


def cleanup(path):
    # fd-based rmtree does not follow links planted by a compromised account.
    import shutil
    if path.parent != state.WORK or not re.fullmatch('[0-9a-f]{32}', path.name): raise ValueError('Invalid work directory')
    if path.is_symlink() or not shutil.rmtree.avoids_symlink_attacks: raise ValueError('Unsafe work cleanup')
    shutil.rmtree(path)


def account_path():
    # Webuzo may link mysqldump into /usr/bin while WP-CLI invokes its real
    # MariaDB name. Include the active native client's root-owned bin directory.
    directories = []
    for name in ('mysqldump', 'mysql', 'mariadb-dump', 'mariadb'):
        for base in ('/usr/local/bin', '/usr/bin'):
            candidate = Path(base) / name
            if candidate.exists():
                target = candidate.resolve(strict=True)
                state.trusted(target); state.trusted(target.parent, True)
                if not os.access(target, os.X_OK): continue
                if str(target.parent) not in directories: directories.append(str(target.parent))
                break
    return ':'.join(directories + ENV['PATH'].split(':'))


def run_as(account, arguments, cwd, timeout=300, output=None, database=False):
    unix = pwd.getpwnam(account['owner'])
    if (unix.pw_uid == 0 or unix.pw_name == 'webuzo' or unix.pw_uid != account['uid'] or
            unix.pw_gid != account['gid'] or unix.pw_dir != account['home']):
        raise ValueError('Hosting account changed; refresh sites before updating')
    env = {**ENV, 'PATH': account_path() if database else ENV['PATH'], 'HOME': str(cwd), 'USER': unix.pw_name, 'LOGNAME': unix.pw_name,
           'WP_CLI_CONFIG_PATH': '/dev/null', 'WP_CLI_DISABLE_AUTO_CHECK_UPDATE': '1',
           'WP_CLI_CACHE_DIR': str(cwd / 'cache'), 'WP_CLI_PACKAGES_DIR': str(cwd / 'packages')}
    # Backups use an inherited, already-open root-private output descriptor. Limit
    # that file to available space while reserving 256 MiB for the running host.
    cap = None
    if output is not None:
        available = os.fstatvfs(output.fileno())
        cap = available.f_bavail * available.f_frsize - 256 * 1024 * 1024
        if cap <= 0: raise RuntimeError('Insufficient free space for a backup')
    def limits():
        if cap is not None: resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
    with subprocess.Popen([str(a) for a in arguments], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=output if output is not None else subprocess.PIPE, stderr=subprocess.PIPE,
            user=unix.pw_uid, group=unix.pw_gid, extra_groups=[], umask=0o077,
            start_new_session=True, preexec_fn=limits) as child:
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                if child.stdout: selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise RuntimeError('Site command timed out; inspect the site before retrying')
                    for key, unused in selector.select(min(remaining, 1)):
                        block = os.read(key.fileobj.fileno(), 65536)
                        if not block:
                            selector.unregister(key.fileobj); continue
                        buffers[key.data].extend(block)
                        if sum(len(b) for b in buffers.values()) > 4 * 1024 * 1024:
                            raise RuntimeError('Site command output exceeded 4 MiB; inspect the site before retrying')
                child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except Exception:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait()
            raise
        stdout, stderr = bytes(buffers['stdout']), bytes(buffers['stderr'])
        if child.returncode:
            message = (stderr or stdout or b'Site command failed').decode('utf-8', errors='replace')[-3000:]
            raise RuntimeError('Site command failed: ' + message)
        return (stdout or b'').decode('utf-8', errors='replace')


def account_task(account, operation, cwd, path=None, output=None):
    args = ['/usr/bin/python3', '-I', '-B', SOURCE / 'account_task.py', operation, account['home']]
    if path is not None: args.append(path)
    return run_as(account, args, cwd, output=output)


def wp(site, args, cwd, timeout=600, output=None):
    account_task(site, 'check', cwd, site['path'])
    if args[:2] == ['db', 'export']:
        # WP-CLI intentionally skips client option files. Match PHP's local
        # socket when DB_HOST is localhost; preserve explicit remote/socket hosts.
        connection = json.loads(wp(site, ['eval', "echo json_encode(array('host'=>DB_HOST,'socket'=>ini_get('mysqli.default_socket')));"], cwd))
        if not isinstance(connection, dict) or set(connection) != {'host', 'socket'}:
            raise ValueError('Cannot determine the WordPress database connection')
        socket = connection['socket']
        if connection['host'] == 'localhost' and socket:
            if not isinstance(socket, str) or not socket.startswith('/') or len(socket) > 4096 or any(ord(c) < 32 for c in socket):
                raise ValueError('Invalid local database socket')
            args = [*args, '--socket=' + socket]
    # This process is already the account UID before PHP or wp-config.php loads.
    return run_as(site, [site['php_bin'], wp_cli(), '--path=' + site['path'], '--skip-plugins',
        '--skip-themes', '--skip-packages', '--no-color', *args], cwd, timeout, output, database=args[:2] == ['db', 'export'])


@functools.lru_cache(maxsize=4096)
def compare(a, b):
    result = subprocess.run([str(PANEL_PHP), '-n', '-r', 'echo version_compare($argv[1],$argv[2]);', '--', str(a), str(b)],
        env=ENV, capture_output=True, text=True, timeout=10, check=True)
    return int(result.stdout)


INVENTORY = '''require_once ABSPATH.'wp-admin/includes/plugin.php';
$p=array();foreach(get_plugins() as $file=>$v){$p[]=array('file'=>$file,'name'=>$v['Name'],'version'=>$v['Version'],'active'=>is_plugin_active($file));}
$t=array();foreach(wp_get_themes() as $slug=>$v){$t[]=array('slug'=>$slug,'version'=>$v->get('Version'));}
echo json_encode(array('core'=>get_bloginfo('version'),'php'=>PHP_VERSION,'plugins'=>$p,'themes'=>$t,'multisite'=>is_multisite(),
'layout_ok'=>WP_CONTENT_DIR===ABSPATH.'wp-content' && WP_PLUGIN_DIR===WP_CONTENT_DIR.'/plugins' && get_theme_root()===WP_CONTENT_DIR.'/themes'));'''


def inventory(site, cwd):
    value = json.loads(wp(site, ['eval', INVENTORY], cwd))
    if not isinstance(value, dict) or set(value) != {'core', 'php', 'plugins', 'themes', 'multisite', 'layout_ok'}:
        raise ValueError('Invalid WordPress inventory response')
    if not isinstance(value['plugins'], list) or not isinstance(value['themes'], list): raise ValueError('Invalid site inventory')
    if value['layout_ok'] is not True: raise ValueError('Custom content, plugin or theme directories are not supported')
    if type(value['multisite']) is not bool: raise ValueError('Invalid WordPress network inventory')
    del value['layout_ok']
    if any(not isinstance(value[k], str) or len(value[k]) > 128 for k in ('core', 'php')):
        raise ValueError('Invalid site runtime version')
    import catalog
    for plugin in value['plugins']:
        if not isinstance(plugin, dict): raise ValueError('Invalid plugin inventory')
        if not isinstance(plugin.get('file'), str) or len(plugin['file'].split('/')) not in (1, 2): raise ValueError('Unsupported plugin layout')
        for piece in plugin['file'].split('/'): catalog.component(piece)
        if not isinstance(plugin.get('version'), str) or len(plugin['version']) > 128: raise ValueError('Invalid installed plugin version')
    for theme in value['themes']:
        if not isinstance(theme, dict) or not isinstance(theme.get('version'), str): raise ValueError('Invalid theme inventory')
        catalog.component(theme['slug'])
    return value


def discover(progress):
    owners = accounts()
    domains = native('domains')
    result, errors = {}, []
    for account in owners.values():
        scratch = work(account)
        try:
            paths = json.loads(account_task(account, 'discover', scratch))
            for path in paths:
                site = {**account, 'path': path, 'id': hashlib.sha256((account['owner'] + '\0' + path).encode()).hexdigest()[:32]}
                matching = [d for d in domains if d['user'] == account['owner'] and
                            (path == d['path'] or path.startswith(d['path'].rstrip('/') + '/'))]
                matching.sort(key=lambda d: -len(d['path']))
                if not matching: continue
                domain = matching[0]
                site.update(domain=domain['domain'])
                try:
                    site['php_bin'] = runtime(domain.get('php_version') or '')
                    progress('Reading ' + site['domain'])
                    site.update(inventory(site, scratch))
                except Exception as error:
                    site['error'] = str(error)
                result[site['id']] = site
        except Exception as error:
            errors.append(dict(owner=account['owner'], error=str(error)))
        finally: cleanup(scratch)
    return dict(sites=result, errors=errors)
