"""Private immutable ZIP packages; only a small header is read, never executed."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

import state

MAX_ZIP = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_FILES = 100000
SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
VERSION = re.compile(r'[0-9A-Za-z][0-9A-Za-z._+\-]{0,63}\Z')


def component(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value) or value in ('.', '..'):
        raise ValueError('Invalid plugin identity')
    return value


def inspect(path):
    if Path(path).stat().st_size > MAX_ZIP: raise ValueError('ZIP exceeds 256 MiB')
    candidates, roots, seen, expanded = [], set(), set(), 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES: raise ValueError('ZIP contains too many files')
        for entry in entries:
            name = entry.filename
            pieces = PurePosixPath(name).parts
            if (not pieces or name.startswith('/') or '\\' in name or ':' in name or
                    any(p in ('', '.', '..') for p in name.rstrip('/').split('/')) or any(ord(c) < 32 for c in name)):
                raise ValueError('ZIP contains an unsafe path')
            if name in seen: raise ValueError('ZIP contains duplicate paths')
            seen.add(name)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError('ZIP links and special files are not supported')
            if entry.flag_bits & 1: raise ValueError('Encrypted ZIP entries are not supported')
            expanded += entry.file_size
            if expanded > MAX_EXPANDED: raise ValueError('ZIP expands beyond 1 GiB')
            if pieces[0] == '__MACOSX' or pieces[-1] == '.DS_Store': continue
            roots.add(pieces[0])
            if len(pieces) != 2 or not pieces[1].lower().endswith('.php') or entry.is_dir(): continue
            with archive.open(entry) as stream:
                head = stream.read(8192).decode('utf-8', errors='replace')
            values = {}
            for field in ('Plugin Name', 'Version', 'Author', 'Plugin URI', 'Requires PHP', 'Requires at least', 'Requires Plugins'):
                match = re.search(r'^\s*(?:/\*+|\*|//|#)?\s*' + re.escape(field) + r':\s*([^\r\n]*)', head, re.I | re.M)
                values[field] = re.sub(r'\s*\*/\s*$', '', match[1]).strip() if match else ''
            if values['Plugin Name']:
                component(pieces[0]); component(pieces[1])
                if not VERSION.fullmatch(values['Version']): raise ValueError('Plugin version is missing or invalid')
                if any(ord(c) < 32 for c in values['Plugin Name']): raise ValueError('Invalid plugin name')
                candidates.append(dict(slug=pieces[0], plugin_file='/'.join(pieces), name=values['Plugin Name'],
                    version=values['Version'], author=values['Author'], requires_php=values['Requires PHP'],
                    requires_wp=values['Requires at least'], requires_plugins=values['Requires Plugins']))
        if len(candidates) != 1 or roots != {candidates[0]['slug']}:
            raise ValueError('Upload one installable plugin ZIP containing one main plugin header; vendor bundles must be unpacked first')
        bad = archive.testzip()
        if bad: raise ValueError('ZIP integrity check failed')
    with open(path, 'rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return {**candidates[0], 'sha256': digest.hexdigest(), 'bytes': Path(path).stat().st_size}


def entries():
    return state.read(state.ROOT / 'catalog.json', {})


def plugin_slug(file):
    pieces=file.split('/') if isinstance(file,str) else []
    if len(pieces) not in (1,2):raise ValueError('Invalid installed plugin file')
    for piece in pieces:component(piece)
    return pieces[0] if len(pieces)==2 else component(Path(file).stem)


def queue_pending(plugin,site,reason,target_version=''):
    slug=plugin_slug(plugin['file'])
    if reason not in ('license_required','download_authorization','package_unavailable'):raise ValueError('Invalid premium detection reason')
    name=plugin['name']
    if not isinstance(name,str) or not name or len(name)>512:raise ValueError('Invalid plugin name')
    state.identity(site['id'])
    if not isinstance(target_version,str) or not VERSION.fullmatch(target_version):target_version=''
    # The worker shares the catalog with authenticated uploads. Always merge the
    # latest state under the same lock so a finished upload cannot be overwritten.
    with state.locked():
        current=entries();record=current.get(slug)
        if record and (not record.get('pending') or record['plugin_file']!=plugin['file']):return False
        if not record:
            record=dict(slug=slug,plugin_file=plugin['file'],name=name,version='',author='',pending=True,
                        requires_php='',requires_wp='',requires_plugins='',detected_at=time.time(),source_sites={})
        record['reason']=reason
        record['source_sites'][site['id']]=dict(installed_version=plugin['version'],requested_version=target_version)
        current[slug]=record;state.save(state.ROOT/'catalog.json',current)
    return True


def remove(slug):
    component(slug)
    current=entries()
    if slug not in current:raise ValueError('This plugin is not in the premium library')
    # Only this plugin's named ZIPs in the root-private package store qualify.
    # Include older uploaded versions; never recurse into a user-provided path.
    store=state.ROOT/'packages';state.trusted(store,True)
    paths=[]
    for folder in store.iterdir():
        if not re.fullmatch('[0-9a-f]{64}',folder.name):continue
        state.trusted(folder,True)
        for path in folder.iterdir():
            if not path.name.startswith(slug+'-') or not path.name.endswith('.zip'):continue
            if not VERSION.fullmatch(path.name[len(slug)+1:-4]):continue
            state.trusted(path)
            if path.stat().st_nlink!=1:raise ValueError('Unexpected package hardlink')
            # A similarly prefixed plugin may contain a valid hyphenated version.
            # Confirm identity from its non-executable ZIP header before removal.
            if inspect(path)['slug']==slug:paths.append(path)
    del current[slug]
    state.save(state.ROOT/'catalog.json',current)
    warnings=[]
    for path in paths:
        try:
            path.unlink()
            if not any(path.parent.iterdir()):path.parent.rmdir()
        except OSError as error:warnings.append(path.name+': '+str(error))
    return dict(removed=len(paths)-len(warnings),warnings=warnings)


def package(record):
    component(record['slug'])
    if not VERSION.fullmatch(record['version']) or not re.fullmatch('[0-9a-f]{64}', record['sha256']):
        raise ValueError('Invalid stored package identity')
    path = state.ROOT / 'packages' / record['sha256'] / (record['slug'] + '-' + record['version'] + '.zip')
    state.trusted(path)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''): digest.update(chunk)
    if digest.hexdigest() != record['sha256']: raise ValueError('Stored package checksum mismatch')
    return path


def commit(source, expected=None):
    state.trusted(Path(source))
    metadata = inspect(source)
    current = entries()
    if expected is not None:
        component(expected)
        if expected not in current or current[expected]['plugin_file'] != metadata['plugin_file']:
            raise ValueError('Replacement ZIP must contain the same plugin directory and main file')
    elif metadata['slug'] in current:
        existing=current[metadata['slug']]
        if not existing.get('pending'):raise ValueError('This plugin is already in the library; use Replace ZIP')
        if existing['plugin_file']!=metadata['plugin_file']:raise ValueError('ZIP must contain the same plugin directory and main file as the waiting entry')
    destination = state.ROOT / 'packages' / metadata['sha256']
    state.mkdir(destination)
    target = destination / (metadata['slug'] + '-' + metadata['version'] + '.zip')
    if not target.exists():
        state.trusted(Path(source))
        os.replace(source, target)
        os.chmod(target, 0o600)
    metadata['uploaded_at'] = time.time()
    current[metadata['slug']] = metadata
    state.save(state.ROOT / 'catalog.json', current)
    return metadata
