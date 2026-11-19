"""Portable upload-policy checks; native filesystem security is tested on Linux."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import zipfile


class CatalogUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.documents = {}
        state = types.SimpleNamespace(
            ROOT=self.base / 'data', trusted=Mock(),
            mkdir=lambda path: path.mkdir(parents=True, exist_ok=True),
            read=lambda path, default=None: copy.deepcopy(self.documents.get(path, default)),
            save=lambda path, value: self.documents.__setitem__(path, copy.deepcopy(value)))
        # Load the real catalog with only its platform-specific state boundary
        # substituted, without changing other tests' native module imports.
        spec = importlib.util.spec_from_file_location('upload_policy_catalog',
            Path(__file__).resolve().parents[1] / 'src' / 'catalog.py')
        self.catalog = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, state=state):
            spec.loader.exec_module(self.catalog)

    def plugin_zip(self, version='2.0', slug='premium', main='plugin.php'):
        # Download filenames deliberately carry no plugin identity or version.
        path = self.base / 'vendor download.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(zipfile.ZipInfo(slug + '/' + main, (2026, 1, 1, 0, 0, 0)),
                '<?php\n/*\nPlugin Name: Premium\nVersion: ' + version + '\n*/\n')
        return path

    def test_add_automatically_replaces_and_accepts_repeated_or_older_versions(self):
        unrelated = self.catalog.commit(self.plugin_zip(slug='other'))
        first = self.catalog.commit(self.plugin_zip('1.0'))
        first_path = self.catalog.package(first)
        for version in ('3.0', '3.0', '2.0'):
            current = self.catalog.commit(self.plugin_zip(version))
            self.assertEqual(self.catalog.entries()['premium'], current)
            self.assertEqual(current['version'], version)
            self.assertEqual(self.catalog.entries()['other'], unrelated)
            self.assertEqual(set(self.catalog.entries()), {'premium', 'other'})
        self.assertTrue(first_path.is_file())
        self.assertEqual(self.catalog.inspect(first_path)['version'], '1.0')

    def test_conflicting_identity_is_rejected_before_storage(self):
        first = self.catalog.commit(self.plugin_zip())
        for expected, slug, main in ((None, 'premium', 'other.php'),
                ('premium', 'premium', 'other.php'), ('premium', 'other', 'plugin.php')):
            with self.subTest(expected=expected, slug=slug, main=main):
                source = self.plugin_zip(slug=slug, main=main)
                with self.assertRaises(ValueError):
                    self.catalog.commit(source, expected)
                self.assertTrue(source.is_file())
                self.assertEqual(self.catalog.entries(), {'premium': first})

    def test_explicit_replacement_remains_available(self):
        first = self.catalog.commit(self.plugin_zip('1.0'))
        current = self.catalog.commit(self.plugin_zip('2.0'), first['slug'])
        self.assertEqual(self.catalog.entries()['premium'], current)
        self.assertEqual(current['version'], '2.0')

    def test_pending_entry_is_filled_by_matching_upload(self):
        self.catalog.state.save(self.catalog.state.ROOT / 'catalog.json', {
            'premium': dict(slug='premium', plugin_file='premium/plugin.php', pending=True)})
        source = self.plugin_zip(main='other.php')
        with self.assertRaises(ValueError):
            self.catalog.commit(source)
        self.assertTrue(self.catalog.entries()['premium']['pending'])
        current = self.catalog.commit(self.plugin_zip())
        self.assertFalse(current.get('pending'))
        self.assertEqual(self.catalog.entries(), {'premium': current})

    def test_invalid_zip_preserves_current_package(self):
        first = self.catalog.commit(self.plugin_zip())
        source = self.base / 'broken.zip'
        source.write_bytes(b'not a ZIP')
        with self.assertRaises(zipfile.BadZipFile):
            self.catalog.commit(source)
        self.assertEqual(self.catalog.entries(), {'premium': first})
        self.assertTrue(self.catalog.package(first).is_file())


if __name__ == '__main__':
    unittest.main()
