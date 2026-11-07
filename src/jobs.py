"""Serialized jobs; a failed selected backup stops that site's updates."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base64
import hashlib
import json
import os
import secrets
import time

import catalog
import host
import state
import plugin_updates
import workers

OPTIONS = ('core', 'plugins', 'premium', 'themes', 'backup_plugins', 'backup_themes', 'backup_database')



def settings():
    saved = state.read(state.ROOT / 'settings.json', {})
    return dict(keep_backups=saved.get('keep_backups', 1),
                options={key:saved.get('options', {}).get(key, True) for key in OPTIONS})

def save_settings(options, keep_backups):
    if not isinstance(options, dict) or set(options) != set(OPTIONS) or any(type(value) is not bool for value in options.values()):
        raise ValueError('Invalid update or backup options')
    if type(keep_backups) is not int or not 1 <= keep_backups <= 10:
        raise ValueError('Choose 1 to 10 backups to keep')
    saved = dict(options=options, keep_backups=keep_backups)
    state.save(state.ROOT / 'settings.json', saved)
    return saved


def directory(identity):
    path = state.ROOT / 'jobs' / state.identity(identity)
    state.trusted(path, True)
    return path


def current():
    identity = state.read(state.ROOT / 'latest.json', {}).get('id')
    if not identity: return None
    path = directory(identity)
    job = state.read(path / 'status.json')
    if job['state'] in ('queued', 'running'):
        pid = job.get('pid')
        if pid:
            try:
                command = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
                script = job.get('worker_script')
                scripts = [script] if script else [str(Path(__file__).resolve()),
                    '/usr/local/webuzo/plugins/mass-wp-update-webuzo/src/jobs.py']
                if not any(value.encode() in command for value in scripts) or identity.encode() not in command:
                    raise ProcessLookupError()
            except (FileNotFoundError, ProcessLookupError):
                job.update(state='failed', message='Worker stopped before completing. Review the recorded site results before retrying.')
                state.save(path / 'status.json', job)
    return job


def start(kind, ids=None, options=None, premium=None, keep_backups=None):
    previous = current()
    if previous and previous['state'] in ('queued', 'running'): raise ValueError('A job is already running; wait for it to finish')
    if kind not in ('scan', 'update'): raise ValueError('Invalid job kind')
    source = state.read(state.ROOT / 'inventory.json', {'sites': {}})['sites']
    packages = catalog.entries()
    request = dict(kind=kind)
    if kind == 'update':
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in source for i in ids):
            raise ValueError('Select sites from the current inventory')
        if not isinstance(options, dict) or set(options) != set(OPTIONS) or any(type(v) is not bool for v in options.values()):
            raise ValueError('Invalid update or backup options')
        if not any(options.values()): raise ValueError('Choose at least one update or backup action')
        if keep_backups is None:keep_backups=state.read(state.ROOT/'settings.json',{}).get('keep_backups',1)
        if type(keep_backups) is not int or not 1<=keep_backups<=10:raise ValueError('Choose 1 to 10 backups to keep')
        if premium is not None:
            catalog.component(premium)
            if premium not in packages: raise ValueError('Unknown premium plugin')
            if packages[premium].get('pending'):raise ValueError('Upload the premium ZIP before updating this plugin')
            if not options['premium'] or any(options[key] for key in ('core', 'plugins', 'themes')):
                raise ValueError('A premium-plugin action may update only that plugin')
            packages = {premium: packages[premium]}
        request.update(sites=[source[i] for i in dict.fromkeys(ids)], options=options, packages=packages,keep_backups=keep_backups)
    identity = secrets.token_hex(16)
    path = state.ROOT / 'jobs' / identity
    state.mkdir(path)
    state.save(path / 'request.json', request)
    job = dict(id=identity, kind=kind, state='queued', message='Queued', created=time.time(), results=[])
    if kind=='update':job.update(site_index=0,site_total=len(request['sites']))
    state.save(path / 'status.json', job)
    job['worker_script']=str(workers.pin()/'jobs.py')
    argv = ['/usr/bin/python3', '-I', '-B', job['worker_script'], '--worker', identity]
    actions = [(os.POSIX_SPAWN_OPEN, fd, os.devnull, flags, 0o600)
               for fd, flags in ((0, os.O_RDONLY), (1, os.O_WRONLY), (2, os.O_WRONLY))]
    pid = os.posix_spawn(argv[0], argv, host.ENV, file_actions=actions, setsid=True)
    job['pid'] = pid
    state.save(path / 'status.json', job)
    state.save(state.ROOT / 'latest.json', {'id': identity})
    if kind=='update' and premium is None:
        saved=settings();saved['keep_backups']=keep_backups
        state.save(state.ROOT/'settings.json',saved)
    return job


def backup_record(value,expected):
    import re
    if (not isinstance(value,dict) or set(value)!={'name','bytes','sha256'} or value['name']!=expected
            or type(value['bytes']) is not int or value['bytes']<=0
            or not isinstance(value['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['sha256'])):
        raise ValueError('Invalid account backup response')
    return value


def backups(site, request, path, scratch, progress, result):
    options=request['options']
    selected=[kind+'.zip' for kind in ('plugins','themes','database') if options['backup_'+kind]]
    if not selected:return
    host.private_backup_home(site)
    location=site['id']+'/'+time.strftime('%Y/%m/%d/%H%M%S-')+path.name
    result['backup_storage']=dict(location=location,directory=str(Path(site['home'])/'.masswpupdate'/location))
    host.backup_task(site,'prepare',scratch,dict(location=location,selected=selected))
    for kind in ('plugins', 'themes', 'database'):
        if not options['backup_' + kind]: continue
        progress('Backing up ' + kind + ' for ' + site['domain'])
        saved=host.backup_task(site,'archive',scratch,dict(location=location,kind=kind))
        result['backups'].append(backup_record(saved['file'],kind+'.zip'))
        result['backup_manifest']=backup_record(saved['manifest'],'manifest.json')
    saved=host.backup_task(site,'commit',scratch,dict(location=location))
    result['backup_manifest']=backup_record(saved['manifest'],'manifest.json')
    try:
        retired=host.backup_task(site,'prune',scratch,dict(location=location,keep=request.get('keep_backups',1)))
        index=state.read(state.ROOT/'retired-backups.json',{})
        import account_backups
        for removed in retired['removed']:
            if not isinstance(removed,str) or not account_backups.SNAPSHOT.fullmatch(removed) or not removed.startswith(site['id']+'/') or removed==location:
                raise ValueError('Invalid backup retention response')
            index[removed]=time.time()
        state.save(state.ROOT/'retired-backups.json',index)
        if retired['warnings']:result.setdefault('warnings',[]).append('Some older backups were kept: '+'; '.join(retired['warnings']))
    except Exception as error:
        result.setdefault('warnings',[]).append('Backup completed, but older backups could not be pruned: '+str(error))


def premium_plan(site, inventory, packages):
    installed = {p['file']: p for p in inventory['plugins']}
    names = {p['file'].split('/')[0] for p in inventory['plugins']}
    plan, skipped = [], []
    for record in packages.values():
        plugin = installed.get(record['plugin_file'])
        if not plugin:
            skipped.append(record['name'] + ': not installed'); continue
        if record.get('pending'):
            skipped.append(record['name'] + ': waiting for premium ZIP');continue
        if host.compare(record['version'], plugin['version']) <= 0:
            skipped.append(record['name'] + ': installed version is current or newer'); continue
        if record['requires_php'] and host.compare(inventory['php'], record['requires_php']) < 0:
            raise ValueError(record['name'] + ': installed PHP does not meet the package requirement')
        if record['requires_wp'] and host.compare(inventory['core'], record['requires_wp']) < 0:
            raise ValueError(record['name'] + ': WordPress does not meet the package requirement')
        dependencies = [part.strip() for part in record['requires_plugins'].split(',') if part.strip()]
        if any(dep not in names for dep in dependencies): raise ValueError(record['name'] + ': a required plugin is missing')
        plan.append(record)
    return plan, skipped


def update_site(site, request, path, progress):
    scratch = None
    result = dict(site=site['id'], domain=site['domain'], owner=site['owner'], state='running', actions=[], backups=[], skipped=[])
    try:
        host.refresh_site(site)
        if site.get('error'): raise ValueError('Refresh sites after resolving this site\'s inventory error')
        scratch=host.work(site)
        before = host.inventory(site, scratch)
        result['before'] = before['core']
        backups(site, request, path, scratch, progress, result)
        options = request['options']
        # Regular updates exclude all packages in the library, preserving ZIP control.
        commands = []
        if options['core']:
            commands += [('WordPress core', ['core', 'update']),
                         ('WordPress database', ['core', 'update-db'] + (['--network'] if before.get('multisite') else []))]
        for title, args in commands:
            progress('Updating ' + title + ' on ' + site['domain'])
            host.wp(site, args, scratch)
            result['actions'].append(title)
        if options['plugins']:plugin_updates.update(site,before,request['packages'],scratch,progress,result)
        if options['themes']:
            progress('Updating Themes on '+site['domain'])
            host.wp(site,['theme','update','--all'],scratch);result['actions'].append('Themes')
        current_inventory = host.inventory(site, scratch)
        if options['premium']:
            plan, premium_skipped = premium_plan(site, current_inventory, request['packages'])
            result['skipped'].extend(premium_skipped)
            for record in plan:
                progress('Updating ' + record['name'] + ' on ' + site['domain'])
                source = catalog.package(record)
                target = scratch / (record['slug'] + '-' + record['version'] + '.zip')
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
                with os.fdopen(fd, 'wb') as output, source.open('rb') as package:
                    for block in iter(lambda: package.read(1024 * 1024), b''): output.write(block)
                    os.fchown(output.fileno(), site['uid'], site['gid'])
                host.wp(site, ['plugin', 'install', str(target), '--force'], scratch)
                verified = host.inventory(site, scratch)
                actual = next((p for p in verified['plugins'] if p['file'] == record['plugin_file']), None)
                if not actual or host.compare(actual['version'], record['version']) != 0:
                    raise ValueError(record['name'] + ': installed version verification failed')
                result['actions'].append(record['name'] + ' ' + record['version'])
        final = host.inventory(site, scratch)
        site.update(final); result['after'] = final['core']
        result['state'] = 'complete'
    except Exception as error:
        result.update(state='failed', error=str(error))
    finally:
        if scratch is not None:
            try:host.cleanup(scratch)
            except Exception as error:result.setdefault('warnings',[]).append('Temporary working files could not be removed: '+str(error))
    return result


def worker(identity):
    path = directory(identity)
    request = state.read(path / 'request.json')
    job = state.read(path / 'status.json')
    def progress(message):
        if request['kind'] == 'scan' and (path / 'cancel.json').exists(): raise InterruptedError('Scan stopped')
        job.update(state='running', message=message)
        state.save(path / 'status.json', job)
    try:
        progress('Discovering sites' if request['kind'] == 'scan' else 'Starting updates')
        if request['kind'] == 'scan':
            result = host.discover(progress)
            result['scanned_at'] = time.time()
            state.save(state.ROOT / 'inventory.json', result)
            job['results'] = result['errors']
        else:
            inventory = state.read(state.ROOT / 'inventory.json', {'sites': {}})
            job['site_total']=len(request['sites'])
            for index,site in enumerate(request['sites'],1):
                if (path / 'cancel.json').exists():
                    job['state'] = 'cancelled'; break
                job['site_index']=index
                progress('Processing '+site['domain'])
                result = update_site(site, request, path, progress)
                inventory['sites'][site['id']] = site
                state.save(state.ROOT / 'inventory.json', inventory)
                job['results'].append(result)
                state.save(path / 'status.json', job)
            else: job['state'] = 'complete'
        if request['kind'] == 'scan': job['state'] = 'complete'
        failures = sum(1 for result in job['results'] if result.get('state') == 'failed' or result.get('error'))
        job['message'] = ('Completed with ' + str(failures) + ' failed site(s)' if failures else 'Job completed') if job['state'] == 'complete' else 'Stopped before the next site'
    except InterruptedError:
        job.update(state='cancelled', message='Scan stopped')
    except Exception as error:
        job.update(state='failed', message=str(error))
    job['finished'] = time.time()
    state.save(path / 'status.json', job)


if __name__ == '__main__':
    import sys
    if os.geteuid() != 0 or len(sys.argv) != 3 or sys.argv[1] != '--worker': raise SystemExit('Use the authenticated Webuzo administrator panel')
    # Wait until the launching request has durably recorded its PID.
    with state.locked():
        if not state.read(state.ROOT / 'active.json', {}).get('active'): raise SystemExit('Plugin is inactive')
    worker(state.identity(sys.argv[2]))
