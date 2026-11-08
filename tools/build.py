"""Build only the public native plugin sources; never include local packages."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package_version():
    version = json.loads((ROOT / 'info.json').read_text(encoding='utf-8'))['version']
    header = (ROOT / 'mass-wp-update-webuzo.php').read_text(encoding='utf-8').split('*/', 1)[0]
    found = re.search(r'^[ \t]*Version:[ \t]*([^\r\n]+)', header, re.M)
    if not isinstance(version, str) or not version or found is None or found[1].strip() != version:
        raise ValueError('The PHP plugin Version header must match info.json before packaging')
    return version


def build():
    version = package_version()
    output = ROOT / 'dist'; output.mkdir(exist_ok=True)
    target = output / ('mass-wp-update-webuzo-' + version + '.zip')
    paths = [ROOT / name for name in ('mass-wp-update-webuzo.php', 'info.json', 'LICENSE', 'README.md', 'UPSTREAM')]
    for folder in ('src', 'panel', 'docs'):
        paths += [path for path in (ROOT / folder).rglob('*') if path.is_file() and
                  '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.zip', '.phar')]
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', max(path.stat().st_mtime for path in paths)))
    date = datetime.fromtimestamp(epoch, timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_symlink(): raise ValueError('Source symlinks are not distributable')
            info = zipfile.ZipInfo('mass-wp-update-webuzo/' + path.relative_to(ROOT).as_posix(), date)
            info.external_attr = 0o100644 << 16; info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    print(target.name, hashlib.sha256(target.read_bytes()).hexdigest())
    return target


if __name__ == '__main__': build()
