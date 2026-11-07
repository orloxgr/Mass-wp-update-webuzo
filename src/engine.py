"""Whitelisted administrator actions. Client input never becomes a command."""
import base64
import json
import os
import secrets
import time

import catalog
import host
import jobs
import state
import transport
import account_backups

FIELDS = {'status': set(), 'job_status': set(), 'scan': set(), 'update': {'sites', 'options', 'premium','keep_backups'},
          'history': {'before'}, 'job_details': {'job'},
          'cancel': {'job'}, 'upload_begin': {'size', 'replace'},
          'upload_chunk': {'upload', 'part', 'offset'}, 'upload_finish': {'upload'}, 'upload_abort': {'upload'},
          'download_prepare': {'job', 'site', 'name'}, 'delete_backups': {'site'}, 'delete_premium': {'slug'}}


def decorate_job(job):
    if job:
        retired=state.read(state.ROOT/'retired-backups.json',{})
        for result in job.get('results',[]):
            if result.get('backup_storage',{}).get('location') in retired:result['backup_removed']=True
    return job


def retire(site,removed):
    index=state.read(state.ROOT/'retired-backups.json',{})
    for location in removed:
        if not isinstance(location,str) or not account_backups.SNAPSHOT.fullmatch(location) or not location.startswith(site+'/'):
            raise ValueError('Invalid backup removal response')
        index[location]=time.time()
    state.save(state.ROOT/'retired-backups.json',index)


def status():
    inventory = state.read(state.ROOT / 'inventory.json', dict(sites={}, errors=[]))
    packages = list(catalog.entries().values())
    for package in packages:
        package['sites'] = []
        for site in inventory['sites'].values():
            plugin = next((p for p in site.get('plugins', []) if p['file'] == package['plugin_file']), None)
            if plugin:
                package['sites'].append(dict(id=site['id'], domain=site['domain'], owner=site['owner'],
                    url=site.get('url',''),
                    version=plugin['version'], newer=not package.get('pending') and host.compare(package['version'], plugin['version']) > 0,
                    error=site.get('error', '')))
    job = decorate_job(jobs.current())
    prerequisites = ''
    try: host.wp_cli()
    except Exception as error: prerequisites = str(error)
    return dict(ok=True, inventory=inventory, packages=packages, job=job, prerequisites=prerequisites,
                settings=state.read(state.ROOT/'settings.json',dict(keep_backups=1)))


def upload_path(identity):
    path = state.ROOT / 'uploads' / state.identity(identity)
    state.trusted(path, True)
    return path


def dispatch(request):
    if request.get('actor') != 'root' or request.get('admin') is not True:
        raise PermissionError('Only the Webuzo root administrator can use this plugin')
    action = request.get('action')
    if action not in FIELDS or set(request) - ({'actor', 'admin', 'action'} | FIELDS[action]):
        raise ValueError('Unsupported action or parameters')
    if not state.read(state.ROOT / 'active.json', {}).get('active'): raise ValueError('The plugin is not active')
    if action == 'status': return status()
    if action == 'job_status': return dict(ok=True, job=decorate_job(jobs.current()))
    if action == 'job_details': return dict(ok=True, job=decorate_job(state.read(jobs.directory(request.get('job')) / 'status.json')))
    if action == 'history':
        before = request.get('before')
        if before is not None and (not isinstance(before, list) or len(before) != 2 or
                type(before[0]) not in (int, float) or not isinstance(before[1], str)):
            raise ValueError('Invalid history cursor')
        records = []
        for path in (state.ROOT / 'jobs').iterdir():
            state.identity(path.name)
            value = state.read(jobs.directory(path.name) / 'status.json')
            if value['kind'] != 'update' or value['state'] in ('queued', 'running'): continue
            key = [value['created'], value['id']]
            if before is not None and key >= before: continue
            records.append({k:value[k] for k in ('id', 'created', 'state', 'message')})
        records.sort(key=lambda record:(record['created'],record['id']), reverse=True)
        page = records[:20]
        cursor = [page[-1]['created'],page[-1]['id']] if len(records) > len(page) else None
        return dict(ok=True, jobs=page, next_cursor=cursor)
    if action == 'scan': return dict(ok=True, job=jobs.start('scan'))
    if action == 'update':
        return dict(ok=True, job=jobs.start('update', request.get('sites'), request.get('options'), request.get('premium'),request.get('keep_backups')))
    if action == 'delete_backups':
        current=jobs.current()
        if current and current['state'] in ('queued','running'):raise ValueError('Wait for the current job to finish before deleting backups')
        identity=state.identity(request.get('site'))
        site=state.read(state.ROOT/'inventory.json',dict(sites={}))['sites'].get(identity)
        if not site:raise ValueError('Select a site from the current inventory')
        scratch=host.work(site)
        try:result=host.backup_task(site,'delete_site',scratch,{})
        finally:host.cleanup(scratch)
        retire(identity,result['removed'])
        return dict(ok=True,removed=len(result['removed']),warnings=result['warnings'])
    if action == 'delete_premium':
        current=jobs.current()
        if current and current['kind']=='update' and current['state'] in ('queued','running'):
            raise ValueError('Wait for the update job to finish before deleting premium packages')
        return dict(ok=True,**catalog.remove(request.get('slug')))
    if action == 'cancel':
        current = jobs.current()
        if not current or current['id'] != request.get('job') or current['state'] not in ('queued', 'running'):
            raise ValueError('Select the currently running job')
        state.save(jobs.directory(current['id']) / 'cancel.json', {'requested': time.time()})
        return dict(ok=True, message='Will stop before starting the next site')
    if action == 'upload_begin':
        size = request.get('size')
        if type(size) is not int or not 0 < size <= catalog.MAX_ZIP: raise ValueError('Select a ZIP up to 256 MiB')
        expected = request.get('replace')
        if expected is not None: catalog.component(expected)
        identity = secrets.token_hex(32)[:32]
        path = state.ROOT / 'uploads' / identity; state.mkdir(path)
        state.save(path / 'request.json', dict(size=size, replace=expected, received=0))
        state.write(path / 'data.zip', b'')
        return dict(ok=True, upload=identity)
    if action == 'upload_chunk':
        path = upload_path(request.get('upload'))
        saved = state.read(path / 'request.json')
        offset = request.get('offset')
        if type(offset) is not int or offset != saved['received']: raise ValueError('Upload offset changed; start the upload again')
        chunk = transport.consume(request.get('part'), '.part', 1024 * 1024)
        if not chunk or offset + len(chunk) > saved['size']: raise ValueError('Upload size mismatch')
        target = path / 'data.zip'; state.trusted(target)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, 'ab') as output:
            if os.fstat(output.fileno()).st_size != offset: raise ValueError('Upload content changed')
            output.write(chunk); output.flush(); os.fsync(output.fileno())
        saved['received'] += len(chunk); state.save(path / 'request.json', saved)
        return dict(ok=True, received=saved['received'])
    if action == 'upload_finish':
        path = upload_path(request.get('upload')); saved = state.read(path / 'request.json')
        if saved['received'] != saved['size']: raise ValueError('Upload is incomplete')
        result = catalog.commit(path / 'data.zip', saved['replace'])
        # Only root-owned staging files created by this upload are removed.
        for name in ('data.zip', 'request.json'):
            target = path / name
            if target.exists(): state.trusted(target); target.unlink()
        path.rmdir()
        return dict(ok=True, package=result)
    if action == 'upload_abort':
        path = upload_path(request.get('upload'))
        for name in ('data.zip', 'request.json'):
            target = path / name
            if target.exists(): state.trusted(target); target.unlink()
        path.rmdir()
        return dict(ok=True)
    if action == 'download_prepare':
        job, site = state.identity(request.get('job')), state.identity(request.get('site'))
        name = request.get('name')
        if name not in ('plugins.tar.gz', 'themes.tar.gz', 'database.sql', 'manifest.json','plugins.zip','themes.zip','database.zip'):
            raise ValueError('Invalid backup selection')
        job_path=jobs.directory(job)
        record=state.read(job_path/'status.json')
        result=next((value for value in record.get('results',[]) if value.get('site')==site),None)
        if not result:raise ValueError('No backups recorded for this site and job')
        if result.get('backup_storage'):
            location=result['backup_storage']['location']
            if location in state.read(state.ROOT/'retired-backups.json',{}):raise ValueError('These backups have been deleted')
            if not account_backups.SNAPSHOT.fullmatch(location) or not location.startswith(site+'/'):raise ValueError('Invalid backup location')
            expected=result.get('backup_manifest') if name=='manifest.json' else next((f for f in result.get('backups',[]) if f['name']==name),None)
            expected=jobs.backup_record(expected,name)
            owner=next((value for value in state.read(job_path/'request.json')['sites'] if value['id']==site),None)
            if not owner:raise ValueError('No hosting account recorded for these backups')
            scratch=host.work(owner)
            try:
                receipt=transport.prepare_download(writer=lambda output:host.backup_task(owner,'read',scratch,
                    dict(location=location,name=name,bytes=expected['bytes']),output),expected=expected)
            finally:host.cleanup(scratch)
            return dict(ok=True,download=receipt,filename=site+'-'+name)
        path = state.ROOT / 'backups' / job / site / name
        state.trusted(path)
        return dict(ok=True, download=transport.prepare_download(path), filename=site + '-' + name)
    raise ValueError('Unsupported action')
