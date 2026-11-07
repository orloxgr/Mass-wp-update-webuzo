"""Root-private application data and durable, no-follow writes."""
import contextlib
import fcntl
import grp
import json
import os
import pwd
from pathlib import Path
import secrets
import stat

ROOT = Path('/var/lib/mass-wp-update-webuzo')
RUN = Path('/run/mass-wp-update-webuzo')
WORK = Path('/var/lib/mass-wp-update-webuzo-work')
DOWNLOADS = Path('/var/lib/mass-wp-update-webuzo-downloads')


def root_group_only():
    """Group-write by GID 0 is trusted only when every member is UID 0."""
    try:
        group = grp.getgrgid(0)
        if any(pwd.getpwnam(name).pw_uid != 0 for name in group.gr_mem): return False
        return not any(user.pw_gid == 0 and user.pw_uid != 0 for user in pwd.getpwall())
    except KeyError:
        return False


def trusted(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid managed path')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        reason = None
        if stat.S_ISLNK(info.st_mode): reason = 'symbolic links are not allowed'
        elif info.st_uid != 0: reason = 'the owner must be UID 0'
        elif info.st_mode & 0o002: reason = 'writable by other users'
        elif info.st_mode & 0o020 and (info.st_gid != 0 or not root_group_only()):
            reason = 'group-write is allowed only for GID 0 with exclusively UID 0 members'
        if reason:
            raise ValueError('Untrusted managed path: ' + str(current) + ' (uid=' + str(info.st_uid) +
                ', gid=' + str(info.st_gid) + ', mode=' + oct(stat.S_IMODE(info.st_mode)) + '): ' + reason)
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Unexpected managed file type')
    return path


def mkdir(path, mode=0o700):
    path = Path(path)
    if not path.exists():
        trusted(path.parent, True)
        path.mkdir(mode=mode)
    trusted(path, True)
    if stat.S_IMODE(path.stat().st_mode) != mode:
        raise ValueError('Unexpected application directory permissions')


def write(path, data):
    path = Path(path)
    trusted(path.parent, True)
    if path.exists() or path.is_symlink():
        trusted(path)
    temporary = path.parent / ('.tmp-' + secrets.token_hex(16))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        if temporary.exists(): temporary.unlink()


def save(path, value):
    write(path, (json.dumps(value, ensure_ascii=True, indent=2) + '\n').encode())


def read(path, default=None):
    path = Path(path)
    if not path.exists():
        if path.is_symlink(): raise ValueError('Refusing a symbolic link')
        return default
    trusted(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        return json.load(stream)


@contextlib.contextmanager
def locked():
    trusted(ROOT, True)
    path = ROOT / 'operation.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        trusted(path)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def identity(value):
    import re
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid record identity')
    return value
