"""Linux/root tests use isolated temporary state and synthetic plugin packages."""
import json
import grp
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import catalog
import account_task
import engine
import host
import jobs
import lifecycle
import state
import transport
import account_backups


def plugin_zip(path, version='2.0.0', slug='example-premium', main='plugin.php', extra=None, headers=''):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(slug + '/' + main, '<?php\n/*\nPlugin Name: Example Premium\nVersion: ' + version + '\n' + headers + '*/\n')
        for name, content in (extra or {}).items(): archive.writestr(name, content)
    return path


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() == 0, 'Run on a disposable Linux host as root')
class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mwu-tests-', dir='/var/lib')
        self.base = Path(self.temp.name)
        self.patches = [patch.object(state, 'ROOT', self.base / 'data'), patch.object(state, 'WORK', self.base / 'work'),
                        patch.object(state, 'RUN', self.base / 'run'), patch.object(state,'DOWNLOADS',self.base/'downloads'),
                        patch.object(lifecycle, 'TMPFILE', self.base / 'tmpfiles.conf')]
        for item in self.patches: item.start()
        state.mkdir(state.ROOT)
        for name in ('jobs', 'packages', 'backups', 'uploads'): state.mkdir(state.ROOT / name)
        panel = pwd.getpwnam('webuzo')
        for directory in (state.RUN,state.DOWNLOADS):
            directory.mkdir(mode=0o730);os.chown(directory,0,panel.pw_gid);os.chmod(directory,0o730)
        state.mkdir(state.WORK, 0o711)
        state.save(state.ROOT / 'active.json', {'active': True})

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def api(self, action, **values):
        return engine.dispatch(dict(actor='root', admin=True, action=action, **values))

    def test_metadata_ignores_original_filename(self):
        data = catalog.inspect(plugin_zip(self.base / 'download (19).zip', headers='Requires PHP: 8.0\nRequires at least: 6.5\n'))
        self.assertEqual((data['slug'], data['version'], data['requires_php']), ('example-premium', '2.0.0', '8.0'))
        self.assertEqual(len(data['sha256']), 64)

    def test_plugin_header_is_not_executed(self):
        marker = self.base / 'executed'
        path = plugin_zip(self.base / 'safe.zip', extra={'example-premium/evil.php': '<?php file_put_contents(' + repr(str(marker)) + ',"bad");'})
        catalog.inspect(path)
        self.assertFalse(marker.exists())

    def test_rejects_unsafe_zip_paths(self):
        for name in ('../escape.php', '/absolute.php', 'example-premium/../escape.php', 'example-premium/./bad.php',
                     'example-premium//bad.php', 'example-premium\\evil.php', 'C:/evil.php'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError): catalog.inspect(plugin_zip(self.base / 'bad.zip', extra={name: 'bad'}))

    def test_rejects_symlink_and_duplicate_entries(self):
        path = plugin_zip(self.base / 'bad.zip')
        with zipfile.ZipFile(path, 'a') as archive:
            link = zipfile.ZipInfo('example-premium/link'); link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, '/etc/passwd')
        with self.assertRaises(ValueError): catalog.inspect(path)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('example-premium/plugin.php', '/* Plugin Name: Test\nVersion: 1 */')
            with self.assertWarns(UserWarning): archive.writestr('example-premium/plugin.php', 'duplicate')
        with self.assertRaises(ValueError): catalog.inspect(path)

    def test_nested_zip_assets_are_allowed(self):
        self.assertEqual(catalog.inspect(plugin_zip(self.base / 'nested.zip', extra={'example-premium/lib/assets.zip': b'asset'}))['version'], '2.0.0')

    def test_rejects_vendor_bundle_or_multiple_main_headers(self):
        for extra in ({'docs/readme.txt':'bundle'}, {'example-premium/second.php':'<?php\n/*\nPlugin Name: Second\nVersion: 1.0\n*/'}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError): catalog.inspect(plugin_zip(self.base / 'bundle.zip', extra=extra))

    def test_zip_resource_limits(self):
        path = plugin_zip(self.base / 'large.zip')
        with patch.object(catalog, 'MAX_ZIP', 1):
            with self.assertRaises(ValueError): catalog.inspect(path)
        with patch.object(catalog, 'MAX_EXPANDED', 1):
            with self.assertRaises(ValueError): catalog.inspect(path)
        with patch.object(catalog, 'MAX_FILES', 0):
            with self.assertRaises(ValueError): catalog.inspect(path)

    def test_replacement_keeps_pinned_old_package(self):
        first = catalog.commit(plugin_zip(self.base / 'first.zip', '1.0'))
        second = catalog.commit(plugin_zip(self.base / 'second.zip', '2.0'), first['slug'])
        self.assertTrue(catalog.package(first).is_file())
        self.assertNotEqual(catalog.package(first), catalog.package(second))
        self.assertEqual(catalog.entries()[first['slug']]['version'], '2.0')
        self.assertEqual(stat.S_IMODE(catalog.package(second).stat().st_mode), 0o600)

    def test_replacement_rejects_another_plugin_and_implicit_overwrite(self):
        first = catalog.commit(plugin_zip(self.base / 'first.zip'))
        with self.assertRaises(ValueError): catalog.commit(plugin_zip(self.base / 'second.zip', main='other.php'), first['slug'])
        with self.assertRaises(ValueError): catalog.commit(plugin_zip(self.base / 'third.zip'))

    def test_tampered_stored_package_rejected(self):
        record = catalog.commit(plugin_zip(self.base / 'first.zip'))
        catalog.package(record).write_bytes(b'tampered')
        with self.assertRaises(ValueError): catalog.package(record)

    def test_paths_reject_links_and_group_writable_ancestors(self):
        secret = self.base / 'file'; secret.write_text('secret'); os.chmod(secret, 0o600)
        link = self.base / 'link'; link.symlink_to(secret)
        with self.assertRaises(ValueError): state.trusted(link)
        os.chown(self.base,0,pwd.getpwnam('nobody').pw_gid)
        os.chmod(self.base, 0o770)
        with self.assertRaises(ValueError): state.trusted(secret)
        os.chmod(self.base, 0o700);os.chown(self.base,0,0)

    def test_root_group_writable_native_directory_is_trusted_only_for_root_members(self):
        from types import SimpleNamespace
        directory=self.base/'native-db';directory.mkdir();os.chown(directory,0,0);directory.chmod(0o2775)
        with patch.object(grp,'getgrgid',return_value=SimpleNamespace(gr_mem=['root'])), \
             patch.object(pwd,'getpwall',return_value=[SimpleNamespace(pw_gid=0,pw_uid=0)]):
            self.assertEqual(state.trusted(directory,True),directory)
        with patch.object(grp,'getgrgid',return_value=SimpleNamespace(gr_mem=['nobody'])):
            with self.assertRaisesRegex(ValueError,'gid=0, mode=0o2775'):state.trusted(directory,True)
        with patch.object(grp,'getgrgid',return_value=SimpleNamespace(gr_mem=[])), \
             patch.object(pwd,'getpwall',return_value=[SimpleNamespace(pw_gid=0,pw_uid=65534)]):
            with self.assertRaises(ValueError):state.trusted(directory,True)
        directory.chmod(0o2777)
        with self.assertRaisesRegex(ValueError,'writable by other users'):state.trusted(directory,True)

    def test_non_database_account_work_does_not_inspect_database_clients(self):
        account=pwd.getpwnam('nobody')
        identity=dict(owner=account.pw_name,uid=account.pw_uid,gid=account.pw_gid,home=account.pw_dir)
        self.base.chmod(0o711);scratch=host.work(identity)
        try:
            with patch.object(host,'account_path',side_effect=ValueError('Database client permissions rejected')):
                value=host.run_as(identity,['/usr/bin/python3','-I','-c','import os;print(os.geteuid())'],scratch)
                self.assertEqual(value.strip(),str(account.pw_uid))
                with self.assertRaisesRegex(ValueError,'Database client permissions rejected'):
                    host.run_as(identity,['/usr/bin/python3','-I','-c','print("must not run")'],scratch,database=True)
        finally:host.cleanup(scratch)

    def test_actor_and_action_allowlist(self):
        for actor, admin in (('root', False), ('customer', True), ('root;id', True)):
            with self.assertRaises(PermissionError): engine.dispatch(dict(actor=actor, admin=admin, action='status'))
        with self.assertRaises(ValueError): self.api('shell', command='id')
        with self.assertRaises(ValueError): self.api('scan', command='id')
        state.save(state.ROOT / 'active.json', {'active': False})
        with self.assertRaises(ValueError): self.api('status')

    def test_download_allowlist_and_private_staging(self):
        job, site = 'a' * 32, 'b' * 32
        record=state.ROOT/'jobs'/job;state.mkdir(record)
        state.save(record/'status.json',dict(results=[dict(site=site)]))
        folder = state.ROOT / 'backups' / job; state.mkdir(folder)
        folder /= site; state.mkdir(folder); state.write(folder / 'database.sql', b'example')
        result = self.api('download_prepare', job=job, site=site, name='database.sql')
        temporary = state.DOWNLOADS / (result['download']['token']+'.download')
        self.assertEqual(temporary.read_bytes(), b'example')
        self.assertEqual(temporary.stat().st_uid,pwd.getpwnam('webuzo').pw_uid)
        self.assertEqual(stat.S_IMODE((folder/'database.sql').stat().st_mode),0o600)
        self.assertEqual((folder/'database.sql').stat().st_uid,0)
        for bad in ('../../etc/passwd', '/etc/passwd', 'arbitrary.zip'):
            with self.assertRaises(ValueError): self.api('download_prepare', job=job, site=site, name=bad)
        with self.assertRaises(ValueError): self.api('download_prepare', job='../escape', site=site, name='database.sql')

    def test_history_pagination_keeps_backups_reachable(self):
        for index in range(22):
            identity = format(index,'032x'); path = state.ROOT / 'jobs' / identity; state.mkdir(path)
            state.save(path / 'status.json', dict(id=identity,kind='update',created=index,state='complete',message='Job completed',results=[]))
        first = self.api('history', before=None)
        second = self.api('history', before=first['next_cursor'])
        self.assertEqual((len(first['jobs']),len(second['jobs'])), (20,2))
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.api('job_details',job=format(0,'032x'))['job']['created'],0)
        with self.assertRaises(ValueError): self.api('job_details', job='../../outside')

    def test_db_export_uses_php_local_socket_and_preserves_remote_host(self):
        site = dict(path='/home/example/public_html',php_bin='/usr/local/apps/php83/bin/php')
        for connection, expected in (({'host':'localhost','socket':'/run/database/mysql.sock'},True),
                                     ({'host':'database.example.test:3307','socket':'/run/database/mysql.sock'},False)):
            with patch.object(host, 'account_task'), patch.object(host, 'wp_cli', return_value='/usr/local/bin/wp'), \
                 patch.object(host, 'run_as', side_effect=[json.dumps(connection),'']) as command:
                host.wp(site,['db','export','-','--quiet'],self.base)
                arguments = command.call_args.args[1]
                self.assertEqual('--socket=/run/database/mysql.sock' in arguments,expected)

    def test_inventory_cannot_replace_account_identity_or_supply_paths(self):
        base=dict(core='7.0',php='8.3',plugins=[],themes=[],multisite=False,layout_ok=True)
        for value in ({**base,'owner':'root'}, {**base,'layout_ok':False},
                      {**base,'plugins':[dict(file='../../outside.php',version='1')]}, {**base,'core':[]}):
            with self.subTest(value=value),patch.object(host,'wp',return_value=json.dumps(value)):
                with self.assertRaises(ValueError):host.inventory({},self.base)

    def test_parent_config_is_supported_inside_the_same_account(self):
        home=self.base/'home';home.mkdir();site=home/'public_html';site.mkdir()
        (site/'wp-includes').mkdir();(site/'wp-content').mkdir()
        for path in (home/'wp-config.php',site/'wp-load.php',site/'wp-includes/version.php'):path.write_text('<?php')
        from types import SimpleNamespace
        with patch.object(os,'geteuid',return_value=65534),patch.object(pwd,'getpwuid',return_value=SimpleNamespace(pw_dir=str(home))):
            account_task.check(str(home),str(site))
            (home/'wp-config.php').unlink();(home/'wp-config.php').symlink_to(self.base/'secret')
            with self.assertRaises(ValueError):account_task.check(str(home),str(site))

    def test_backup_failure_keeps_completed_backup_manifest(self):
        identity='a'*32;path=state.ROOT/'jobs'/identity;state.mkdir(path)
        site=dict(id='b'*32,domain='example.test',path='/home/example/public_html',home='/home/example')
        result=dict(backups=[]);calls=[]
        def backup(account, operation, cwd, payload):
            calls.append(operation)
            if operation=='archive' and payload['kind']=='themes':raise RuntimeError('Unreadable theme')
            return dict(file=dict(name='plugins.zip',bytes=100,sha256='c'*64),manifest=dict(name='manifest.json',bytes=200,sha256='d'*64))
        with patch.object(host,'private_backup_home'),patch.object(host,'backup_task',side_effect=backup):
            with self.assertRaisesRegex(RuntimeError,'Unreadable theme'):
                jobs.backups(site,dict(options=dict(backup_plugins=True,backup_themes=True,backup_database=True)),path,self.base,lambda msg:None,result)
        self.assertEqual([f['name'] for f in result['backups']],['plugins.zip'])
        self.assertNotIn('prune',calls);self.assertNotIn('commit',calls)

    def test_retention_rejects_out_of_range_and_non_integer_values(self):
        state.save(state.ROOT/'inventory.json',dict(sites={'a'*32:dict(id='a'*32)}))
        for keep in (0,11,True,'2',1.5):
            with self.subTest(keep=keep),self.assertRaisesRegex(ValueError,'1 to 10'):
                jobs.start('update',['a'*32],{key:True for key in jobs.OPTIONS},keep_backups=keep)

    def test_delete_backups_is_site_scoped_and_refuses_running_jobs(self):
        site=dict(id='b'*32,owner='example',home='/home/example',path='/home/example/public_html')
        state.save(state.ROOT/'inventory.json',dict(sites={site['id']:site}))
        with patch.object(jobs,'current',return_value=dict(state='running')),patch.object(host,'backup_task') as task:
            with self.assertRaisesRegex(ValueError,'current job'):self.api('delete_backups',site=site['id'])
            task.assert_not_called()
        removed=site['id']+'/2026/01/01/120000-'+'c'*32
        with patch.object(host,'work',return_value=self.base),patch.object(host,'cleanup'),patch.object(host,'backup_task',return_value=dict(removed=[removed],warnings=[])) as task:
            self.assertEqual(self.api('delete_backups',site=site['id'])['removed'],1)
            task.assert_called_once_with(site,'delete_site',self.base,{})
        self.assertIn(removed,state.read(state.ROOT/'retired-backups.json'))
        with self.assertRaises(ValueError):self.api('delete_backups',site='../other')
        with self.assertRaises(ValueError):self.api('delete_backups',site='c'*32)
        with self.assertRaises(ValueError):self.api('delete_backups',site=site['id'],path='/etc')

    def test_account_download_rejects_changed_content_and_cleans_staging(self):
        import hashlib
        expected=dict(name='plugins.zip',bytes=4,sha256=hashlib.sha256(b'good').hexdigest())
        before=set(state.DOWNLOADS.iterdir())
        with self.assertRaisesRegex(ValueError,'checksum'):
            transport.prepare_download(writer=lambda output:output.write(b'evil'),expected=expected)
        self.assertEqual(set(state.DOWNLOADS.iterdir()),before)
        result=transport.prepare_download(writer=lambda output:output.write(b'good'),expected=expected)
        self.assertEqual((state.DOWNLOADS/(result['token']+'.download')).read_bytes(),b'good')

    def test_backup_helper_refuses_root_and_nonmatching_site_locations(self):
        with patch.object(sys,'argv',['helper','delete_site','/root',json.dumps(dict(path='/root/site'))]):
            with self.assertRaisesRegex(ValueError,'non-root'):account_backups.main()
        for invalid in ('../etc','a'*32+'/../../etc','a'*32+'/2026/01/01/120000-'+'b'*32+'/extra'):
            with self.assertRaises(ValueError):account_backups.directory('/home/example',invalid)

    def test_backup_home_cannot_be_inside_a_native_document_root(self):
        site=dict(owner='example',home='/home/example')
        with patch.object(host,'native',return_value=[dict(user='example',path='/home/example')]):
            with self.assertRaisesRegex(ValueError,'document root'):host.private_backup_home(site)
        with patch.object(host,'native',return_value=[dict(user='example',path='/home/example/public_html')]):
            host.private_backup_home(site)

    def test_real_account_backup_storage_retention_and_deletion(self):
        account=pwd.getpwnam('nobody')
        identity=dict(owner=account.pw_name,uid=account.pw_uid,gid=account.pw_gid,home=account.pw_dir)
        self.base.chmod(0o711)
        scratch=host.work(identity)
        try:
            value=host.run_as(identity,['/usr/bin/python3','-I','-B',Path(__file__).with_name('backup_storage_probe.py'),scratch],scratch)
            self.assertIn('Unprivileged ZIP',value)
        finally:host.cleanup(scratch)

    def test_chunk_upload_offsets_and_finish(self):
        raw = plugin_zip(self.base / 'input.zip').read_bytes()
        token = self.api('upload_begin', size=len(raw), replace=None)['upload']
        with self.assertRaises(ValueError): self.api('upload_finish', upload=token)
        with self.assertRaises(ValueError): self.api('upload_chunk', upload=token, offset=1, part='f'*64)
        split = len(raw) // 2
        for offset, block in ((0, raw[:split]), (split, raw[split:])):
            with patch.object(transport, 'consume', return_value=block): self.api('upload_chunk', upload=token, offset=offset, part='f'*64)
        self.assertEqual(self.api('upload_finish', upload=token)['package']['version'], '2.0.0')
        self.assertFalse((state.ROOT / 'uploads' / token).exists())

    def test_upload_abort_only_removes_own_staging(self):
        token = self.api('upload_begin', size=1, replace=None)['upload']
        self.api('upload_abort', upload=token)
        self.assertFalse((state.ROOT / 'uploads' / token).exists())
        with self.assertRaises(ValueError): self.api('upload_abort', upload='../packages')

    def test_premium_does_not_install_absent_or_downgrade(self):
        record = catalog.inspect(plugin_zip(self.base / 'input.zip', '2.0'))
        inventory = dict(core='6.8', php='8.3', plugins=[dict(file=record['plugin_file'], version='3.0')])
        with patch.object(host, 'compare', side_effect=lambda a,b: (a>b)-(a<b)):
            plan, skipped = jobs.premium_plan({}, inventory, {record['slug']:record})
            self.assertEqual(plan, []); self.assertTrue(skipped)
            inventory['plugins'] = []
            self.assertEqual(jobs.premium_plan({}, inventory, {record['slug']:record})[0], [])

    def test_requirements_stop_premium_update(self):
        record = catalog.inspect(plugin_zip(self.base / 'input.zip', '2.0', headers='Requires PHP: 9.0\n'))
        inventory = dict(core='6.8', php='8.3', plugins=[dict(file=record['plugin_file'], version='1.0')])
        with patch.object(host, 'compare', side_effect=lambda a,b: (a>b)-(a<b)):
            with self.assertRaises(ValueError): jobs.premium_plan({}, inventory, {record['slug']:record})

    def test_failed_backup_prevents_all_updates(self):
        site = dict(id='a'*32, domain='example.test', owner='example', core='6.8')
        request = dict(options={key:True for key in jobs.OPTIONS}, packages={})
        with patch.object(host, 'work', return_value=self.base), patch.object(host, 'cleanup'), \
             patch.object(host, 'inventory', return_value=dict(core='6.8')), patch.object(jobs, 'backups', side_effect=RuntimeError('Backup failed')), \
             patch.object(host, 'wp') as wp:
            result = jobs.update_site(site, request, self.base, lambda message:None)
            self.assertEqual(result['state'], 'failed'); self.assertIn('Backup failed', result['error']); wp.assert_not_called()

    def test_job_rejects_unknown_sites_and_non_boolean_flags(self):
        with self.assertRaises(ValueError): jobs.start('update', ['a'*32], {key:True for key in jobs.OPTIONS})
        state.save(state.ROOT / 'inventory.json', {'sites': {'a'*32:{'id':'a'*32}}})
        with self.assertRaises(ValueError): jobs.start('update', ['a'*32], {key:1 for key in jobs.OPTIONS})
        with self.assertRaises(ValueError): jobs.start('update', ['a'*32], {key:False for key in jobs.OPTIONS})

    def test_run_as_drops_identity_and_cannot_read_root_secret(self):
        account = pwd.getpwnam('nobody')
        identity = dict(owner=account.pw_name,uid=account.pw_uid,gid=account.pw_gid,home=account.pw_dir)
        os.chmod(self.base, 0o711)
        scratch = host.work(identity)
        secret = self.base / 'root-secret'; secret.write_text('secret'); os.chmod(secret,0o600)
        code = 'import os,sys;print(os.geteuid());\ntry:open(sys.argv[1]);print("bad")\nexcept PermissionError:print("denied")'
        try:
            result = host.run_as(identity, ['/usr/bin/python3','-I','-c',code,str(secret)], scratch)
            self.assertEqual(result.strip().splitlines(), [str(account.pw_uid),'denied'])
        finally: host.cleanup(scratch)

    def test_command_output_limit_and_timeout(self):
        account = pwd.getpwnam('nobody')
        identity = dict(owner=account.pw_name,uid=account.pw_uid,gid=account.pw_gid,home=account.pw_dir)
        os.chmod(self.base, 0o711); scratch = host.work(identity)
        try:
            with self.assertRaisesRegex(RuntimeError, '4 MiB'):
                host.run_as(identity, ['/usr/bin/python3','-I','-c','print("x"*5000000)'], scratch)
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                host.run_as(identity, ['/usr/bin/python3','-I','-c','import time;time.sleep(10)'], scratch, timeout=.1)
        finally: host.cleanup(scratch)

    def test_root_account_is_never_a_wordpress_runtime(self):
        with self.assertRaises(ValueError): host.run_as(dict(owner='root',uid=0,gid=0,home='/root'), ['id'], self.base)

    def test_transport_rejects_untrusted_owner_and_consumes_once(self):
        panel = pwd.getpwnam('webuzo')
        token = 'a'*64; target = state.RUN / (token+'.json'); target.write_text('{}'); os.chmod(target,0o600)
        with self.assertRaises(ValueError): transport.consume(token)
        os.chown(target,panel.pw_uid,panel.pw_gid)
        self.assertEqual(transport.consume(token), b'{}')
        with self.assertRaises(FileNotFoundError): transport.consume(token)

    def test_large_native_response_uses_a_private_file(self):
        token='b'*64;payload=json.dumps({'value':'x'*500000}).encode()
        transport.publish(token,payload)
        target=state.RUN/(token+'.response')
        self.assertEqual(target.read_bytes(),payload)
        self.assertEqual(target.stat().st_uid,pwd.getpwnam('webuzo').pw_uid)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
        with self.assertRaises(FileExistsError): transport.publish(token,b'overwrite')


if __name__ == '__main__': unittest.main()
