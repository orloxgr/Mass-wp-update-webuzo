"""Release metadata must agree before any ZIP is created or replaced."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_builder', ROOT / 'tools/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
PLUGIN = 'mass-wp-update-webuzo'


class PackageVersions(unittest.TestCase):
    def test_repository_metadata_agrees(self):
        self.assertEqual(builder.package_version(), json.loads((ROOT / 'info.json').read_text())['version'])

    def fixture(self, root, header='1.2.3'):
        (root / 'info.json').write_text(json.dumps(dict(version='1.2.3')), encoding='utf-8')
        (root / (PLUGIN + '.php')).write_text('<?php\n/*\nVersion: ' + header + '\n*/\n', encoding='utf-8')
        for name in ('LICENSE', 'README.md', 'UPSTREAM'):
            (root / name).write_text('Fixture source', encoding='utf-8')

    def test_zip_filename_and_both_metadata_sources_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            with patch.object(builder, 'ROOT', root):
                archive = builder.build()
            self.assertEqual(archive.name, PLUGIN + '-1.2.3.zip')
            with zipfile.ZipFile(archive) as bundle:
                info = json.loads(bundle.read(PLUGIN + '/info.json'))
                header = bundle.read(PLUGIN + '/' + PLUGIN + '.php').decode()
                self.assertEqual(info['version'], '1.2.3')
                self.assertIn('Version: ' + info['version'], header.splitlines())

    def test_mismatch_cannot_overwrite_an_existing_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, '1.2.2')
            (root / 'dist').mkdir()
            archive = root / 'dist' / (PLUGIN + '-1.2.3.zip')
            archive.write_bytes(b'existing package')
            with patch.object(builder, 'ROOT', root), self.assertRaisesRegex(ValueError, 'must match info.json'):
                builder.build()
            self.assertEqual(archive.read_bytes(), b'existing package')

    def test_missing_header_stops_before_creating_dist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / (PLUGIN + '.php')).write_text('<?php\n/* No version */\n')
            with patch.object(builder, 'ROOT', root), self.assertRaisesRegex(ValueError, 'must match info.json'):
                builder.build()
            self.assertFalse((root / 'dist').exists())


if __name__ == '__main__':
    unittest.main()
