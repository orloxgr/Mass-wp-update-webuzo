"""Immutable worker generations survive replacement of the native plugin ZIP."""
import hashlib
import grp
import os
from pathlib import Path
import pwd
import shutil
import stat
import tempfile

import state


def trusted_source(path):
    native = Path('/usr/local/webuzo')
    installed = native / 'plugins/mass-wp-update-webuzo/src'
    if path.parent != installed:
        return state.trusted(path)
    # The native panel delegates root commands through wexec and owns a
    # group-writable installation ancestor. Trust that existing panel identity
    # only at its native directory, never at hosting or worker directories.
    panel = pwd.getpwnam('webuzo')
    members = set(grp.getgrgid(panel.pw_gid).gr_mem)
    members.update(user.pw_name for user in pwd.getpwall() if user.pw_gid == panel.pw_gid)
    if panel.pw_uid == 0 or panel.pw_gid == 0 or members - {'root', 'webuzo'}:
        raise ValueError('Unexpected native panel group')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        panel_write = current == native and info.st_gid == panel.pw_gid
        root_write = info.st_gid == 0 and state.root_group_only()
        if (info.st_uid != 0 or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o002 or
                info.st_mode & 0o020 and not (panel_write or root_write)):
            raise ValueError('Untrusted worker source: ' + str(current))
        if not (stat.S_ISREG(info.st_mode) if current == path else stat.S_ISDIR(info.st_mode)):
            raise ValueError('Unexpected worker source type')
    return path


def pin():
    source = Path(__file__).resolve().parent
    files = {}
    for path in sorted(source.glob('*.py')):
        trusted_source(path)
        files[path.name] = path.read_bytes()
    if not {'jobs.py', 'host.py', 'account_task.py', 'account_backups.py'} <= files.keys():
        raise ValueError('Incomplete worker source')
    digest = hashlib.sha256()
    for name, data in files.items():
        digest.update(name.encode() + b'\0' + data)
    store = state.WORK / 'code'
    state.mkdir(store, 0o755)
    generation = store / digest.hexdigest()
    if not generation.exists():
        temporary = Path(tempfile.mkdtemp(prefix='.stage-', dir=store))
        try:
            for name, data in files.items():
                state.write(temporary / name, data)
                (temporary / name).chmod(0o644)
            temporary.chmod(0o755)
            os.replace(temporary, generation)
        finally:
            if temporary.exists(): shutil.rmtree(temporary)
    state.trusted(generation, True)
    for name, data in files.items():
        state.trusted(generation / name)
        if (generation / name).read_bytes() != data:
            raise ValueError('Stored worker source changed')
    return generation
