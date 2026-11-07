"""Filesystem operations that must run as the hosting account, never root."""
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import tarfile


def check(home, path):
    home, path = Path(home), Path(path)
    if os.geteuid() == 0 or Path(pwd.getpwuid(os.geteuid()).pw_dir) != home:
        raise ValueError('A non-root hosting account is required')
    if not path.is_absolute() or '..' in path.parts or path != path.resolve() or not path.is_relative_to(home):
        raise ValueError('WordPress path leaves its account or contains a symbolic link')
    config = path / 'wp-config.php'
    if not config.exists():
        config = path.parent / 'wp-config.php'
        if not config.is_relative_to(home) or (path.parent / 'wp-settings.php').exists(): raise ValueError('WordPress configuration is missing')
    for target in (config, path / 'wp-load.php', path / 'wp-includes/version.php'):
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid()):
            raise ValueError('WordPress structure or ownership mismatch')
    if not (path / 'wp-content').is_dir(): raise ValueError('WordPress content directory is missing')


def main():
    operation, home = sys.argv[1:3]
    if os.geteuid() == 0: raise ValueError('Refusing account work as root')
    if operation == 'discover':
        paths = []
        roots = json.loads(sys.argv[3])
        if not isinstance(roots, list): raise ValueError('Expected native document roots')
        for root in roots:
            target = Path(root)
            if not target.is_absolute() or '..' in target.parts or not target.is_relative_to(Path(home)):
                raise ValueError('A native document root leaves its hosting account')
            if not target.exists(): continue
            if target != target.resolve(): raise ValueError('A document root contains a symbolic link')
            for folder, dirs, files in os.walk(target, followlinks=False):
                dirs[:] = [d for d in dirs if d not in ('wp-content', 'wp-admin', 'wp-includes', 'node_modules', 'vendor', '.git', '.cache', '.masswpupdate')
                           and not (Path(folder) / d).is_symlink()]
                if 'wp-load.php' in files:
                    try: check(home, folder)
                    except (OSError, ValueError): continue
                    if folder not in paths: paths.append(folder)
        print(json.dumps(paths))
    elif operation == 'check':
        check(home, sys.argv[3])
    elif operation in ('plugins', 'themes'):
        path = Path(sys.argv[3]); check(home, path)
        target = path / 'wp-content' / operation
        if target.is_symlink() or not target.is_dir(): raise ValueError('Invalid backup source directory')
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz', dereference=False) as archive:
            archive.add(target, arcname=operation, recursive=True)
    else: raise ValueError('Unknown account operation')


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(str(error), file=sys.stderr); raise SystemExit(1)
