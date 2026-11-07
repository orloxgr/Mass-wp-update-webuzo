"""Regression coverage for the shared Hestia features on native Webuzo."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_backend as backend
import account_task
import catalog
import diagnostics
import engine
import host
import jobs
import php_cli
import plugin_updates
import state
import workers


class PresentationTests(unittest.TestCase):
    def test_database_summary_preserves_complete_original_trace(self):
        raw='Site command failed: mysqli: getaddrinfo for database.example.test failed\nStack trace:\n'+'x'*6000
        record=diagnostics.decorate(dict(error=raw))
        self.assertEqual(record['error'],raw)
        self.assertIn('Cannot resolve database host database.example.test',record['error_summary'])

    def test_cli_export_restores_only_required_process_functions(self):
        self.assertEqual(php_cli.database_flags('exec,system,proc_open,shell_exec,proc_close,proc_get_status'),
                         ['-d','disable_functions=system,shell_exec'])
        self.assertEqual(php_cli.database_flags('system'),[])


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid()==0,'Disposable Linux Webuzo host only')
class PortTests(unittest.TestCase):
    setUp=backend.BackendTests.setUp
    tearDown=backend.BackendTests.tearDown

    def test_valid_wordpress_basenames_and_unsafe_paths(self):
        for value in ('_plugin.php','Plugin name (old).php','Ελληνικά.php','--all'):
            self.assertEqual(catalog.component(value),value)
        for value in ('','.','..','../file','a/b','a\\b','bad\nname','bad\0name','bad\x7fname'):
            with self.subTest(value=value),self.assertRaises(ValueError):catalog.component(value)
        record=catalog.inspect(backend.plugin_zip(self.base/'underscore.zip',slug='Plugin Ελληνικά',main='_plugin file.php'))
        self.assertEqual(record['plugin_file'],'Plugin Ελληνικά/_plugin file.php')

    def test_plugin_named_all_cannot_be_a_command_option(self):
        plugin=dict(file='--all/main.php',name='Option-like plugin',version='1')
        replies=[json.dumps([dict(name='--all',update_version='2')]),'Success']
        with patch.object(host,'wp',side_effect=replies) as command:
            plugin_updates.update(dict(domain='example.test'),dict(plugins=[plugin]),{},self.base,lambda value:None,dict(actions=[]))
        args=command.call_args.args[1]
        self.assertEqual(args[0],'eval')
        self.assertNotIn('--all',args)

    def test_settings_default_themes_and_persist_explicit_false(self):
        self.assertTrue(jobs.settings()['options']['themes'])
        options={key:key!='themes' for key in jobs.OPTIONS}
        saved=jobs.save_settings(options,4)
        self.assertEqual(jobs.settings(),saved)
        with self.assertRaises(ValueError):jobs.save_settings({**options,'core':1},4)

    def test_premium_job_does_not_overwrite_general_settings(self):
        options={key:True for key in jobs.OPTIONS}
        jobs.save_settings(options,3)
        site=dict(id='a'*32)
        state.save(state.ROOT/'inventory.json',dict(sites={site['id']:site}))
        package=catalog.commit(backend.plugin_zip(self.base/'package.zip'))
        temporary={key:key=='premium' for key in jobs.OPTIONS}
        with patch.object(os,'posix_spawn',return_value=12345):
            started=jobs.start('update',[site['id']],temporary,package['slug'],9)
        self.assertEqual(jobs.settings()['options'],options)
        self.assertEqual(jobs.settings()['keep_backups'],3)
        jobs.save_settings({key:False for key in jobs.OPTIONS},2)
        request=state.read(jobs.directory(started['id'])/'request.json')
        self.assertEqual(request['options'],temporary)
        self.assertEqual(request['keep_backups'],9)

    def test_running_worker_uses_immutable_source_and_stored_identity(self):
        first=workers.pin()
        self.assertEqual(first,workers.pin())
        self.assertEqual((first/'host.py').read_bytes(),(Path(host.__file__).parent/'host.py').read_bytes())
        identity='b'*32;path=state.ROOT/'jobs'/identity;state.mkdir(path)
        script=str(first/'jobs.py')
        state.save(path/'status.json',dict(id=identity,state='running',pid=12345,worker_script=script))
        state.save(state.ROOT/'latest.json',dict(id=identity))
        original=Path.read_bytes
        def content(p):
            if str(p)=='/proc/12345/cmdline':return ('python3\0'+script+'\0--worker\0'+identity+'\0').encode()
            return original(p)
        with patch.object(Path,'read_bytes',content):self.assertEqual(jobs.current()['state'],'running')
        # The first upgrade must still recognize a pre-generation worker.
        legacy='/usr/local/webuzo/plugins/mass-wp-update-webuzo/src/jobs.py'
        record=state.read(path/'status.json');record.pop('worker_script');state.save(path/'status.json',record)
        def legacy_content(p):
            if str(p)=='/proc/12345/cmdline':return ('python3\0'+legacy+'\0--worker\0'+identity+'\0').encode()
            return original(p)
        with patch.object(Path,'read_bytes',legacy_content):self.assertEqual(jobs.current()['state'],'running')

    def test_cleanup_failure_preserves_completed_actions(self):
        site=dict(id='a'*32,domain='example.test',owner='example')
        request=dict(options={key:key=='core' for key in jobs.OPTIONS},packages={})
        with patch.object(host,'refresh_site'),patch.object(host,'work',return_value=self.base),\
             patch.object(host,'inventory',return_value=dict(core='7.0')),patch.object(host,'wp'),\
             patch.object(jobs,'backups'),patch.object(host,'cleanup',side_effect=OSError('cleanup denied')):
            result=jobs.update_site(site,request,self.base,lambda value:None)
        self.assertEqual(result['state'],'complete')
        self.assertEqual(len(result['actions']),2)
        self.assertIn('cleanup denied',result['warnings'][0])

    def test_refresh_rejects_changed_or_suspended_domain_and_refreshes_php(self):
        account=dict(owner='example',uid=1001,gid=1001,home='/home/example')
        site={**account,'domain':'example.test','path':'/home/example/public_html/blog','document_root':'/home/example/public_html'}
        domain=dict(user='example',domain='example.test',path=site['document_root'],php_version='php84')
        with patch.object(host,'accounts',return_value={'example':account}),patch.object(host,'runtime',return_value='/native/php84') as runtime:
            with patch.object(host,'native',return_value=[domain]):host.refresh_site(site)
            self.assertEqual(site['php_bin'],'/native/php84')
            runtime.assert_called_with('php84')
            for rows in ([],[{**domain,'suspended':True}],[{**domain,'path':'/home/example'}]):
                with patch.object(host,'native',return_value=rows),self.assertRaises(ValueError):host.refresh_site(site)

    def test_counter_counts_failed_sites_and_recovers_old_history(self):
        identity='c'*32;path=state.ROOT/'jobs'/identity;state.mkdir(path)
        sites=[dict(id=str(i),domain='example.test') for i in range(3)]
        state.save(path/'request.json',dict(kind='update',sites=sites))
        state.save(path/'status.json',dict(id=identity,kind='update',state='queued',results=[]))
        state.save(state.ROOT/'inventory.json',dict(sites={}))
        indices=[]
        def update(site,request,path,progress):
            indices.append(state.read(path/'status.json')['site_index'])
            return dict(site=site['id'],state='failed',error='Example failure')
        with patch.object(jobs,'update_site',side_effect=update):jobs.worker(identity)
        job=state.read(path/'status.json')
        self.assertEqual(indices,[1,2,3])
        self.assertEqual((job['site_index'],job['site_total']),(3,3))
        old=dict(id=identity,kind='update',state='running',results=[dict(error='old failure')])
        self.assertEqual(engine.decorate_job(old)['site_index'],2)
        self.assertIn('error_summary',old['results'][0])

    def test_discovery_receives_only_native_document_roots(self):
        account=dict(owner='example',home='/home/example',uid=1001,gid=1001)
        domain=dict(user='example',domain='example.test',path='/home/example/public_html')
        with patch.object(host,'accounts',return_value={'example':account}),patch.object(host,'native',return_value=[domain]),\
             patch.object(host,'work',return_value=self.base),patch.object(host,'cleanup'),\
             patch.object(host,'account_task',return_value='[]') as helper:
            host.discover(lambda value:None)
        self.assertEqual(json.loads(helper.call_args.args[3]),[domain['path']])
        with patch.object(host,'accounts',return_value={'example':account}),patch.object(host,'native',return_value=[{**domain,'cert':1}]),\
             patch.object(host,'work',return_value=self.base),patch.object(host,'cleanup'),\
             patch.object(host,'account_task',return_value=json.dumps([domain['path']+'/blog'])),\
             patch.object(host,'runtime',return_value='/native/php'),patch.object(host,'inventory',side_effect=RuntimeError('Database unavailable')):
            inventory=host.discover(lambda value:None)
        site=next(iter(inventory['sites'].values()))
        self.assertEqual(site['url'],'https://example.test/blog/')
        self.assertEqual(site['error'],'Database unavailable')

    def test_full_stderr_and_stdout_are_kept(self):
        error=host.SiteCommandError(b'x'*6000,b'original warning')
        self.assertIn('original warning',str(error))
        self.assertTrue(str(error).endswith('x'*6000))


if __name__=='__main__':unittest.main()
