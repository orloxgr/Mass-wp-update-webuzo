#!/usr/bin/python3
import json
import os
from pathlib import Path
import pwd
import stat
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import state

TMPFILE = Path('/etc/tmpfiles.d/mass-wp-update-webuzo.conf')
CONTENT = b'# Managed by mass-wp-update-webuzo\nd /run/mass-wp-update-webuzo 0730 root webuzo -\n'


def install():
    panel = pwd.getpwnam('webuzo')
    if panel.pw_uid == 0 or panel.pw_gid == 0: raise ValueError('Unexpected native panel identity')
    import grp
    members = {p.pw_name for p in pwd.getpwall() if p.pw_gid == panel.pw_gid} | set(grp.getgrgid(panel.pw_gid).gr_mem)
    if members - {'root', 'webuzo'}: raise ValueError('Hosting users must not share the native panel group')
    state.mkdir(state.ROOT)
    for name in ('jobs', 'packages', 'backups', 'uploads'): state.mkdir(state.ROOT / name)
    state.mkdir(state.WORK, 0o711)
    for directory in (state.RUN, state.DOWNLOADS):
        if not directory.exists():
            directory.mkdir(mode=0o730); os.chown(directory, 0, panel.pw_gid); os.chmod(directory, 0o730)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != panel.pw_gid
                or stat.S_IMODE(info.st_mode) != 0o730): raise ValueError('Unexpected panel transport directory')
    if TMPFILE.exists() and TMPFILE.read_bytes() != CONTENT: raise ValueError('Existing tmpfiles entry is not owned by this plugin')
    state.write(TMPFILE, CONTENT); os.chmod(TMPFILE, 0o644)
    state.save(state.ROOT / 'active.json', dict(active=True))


def remove():
    import jobs
    with state.locked():
        current = jobs.current()
        if current and current['state'] in ('queued', 'running'): raise ValueError('Wait for the job to finish before deactivating')
        state.save(state.ROOT / 'active.json', dict(active=False))
    if TMPFILE.exists():
        state.trusted(TMPFILE)
        if TMPFILE.read_bytes() != CONTENT: raise ValueError('Refusing an unrelated tmpfiles entry')
        TMPFILE.unlink()


if __name__ == '__main__':
    try:
        if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('install', 'remove'): raise ValueError('Invalid lifecycle operation')
        (install if sys.argv[1] == 'install' else remove)()
        print(json.dumps(dict(ok=True)))
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error)))); raise SystemExit(1)
