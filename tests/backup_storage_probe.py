"""Exercise real backup files as an unprivileged test account."""
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import account_backups as backups


def run(home):
    assert os.geteuid()!=0
    home=Path(home)
    source=home/'plugins';source.mkdir(mode=0o700)
    (source/'example.php').write_text('<?php /* Plugin Name: Example */')
    def snapshot(site,number,complete=True):
        location=site+'/2026/01/01/120000-'+format(number,'032x')
        fd=backups.directory(str(home),location,True)
        try:
            backups.save_json(fd,dict(schema=1,location=location,complete=False,files=[],selected=['plugins.zip']))
            record=backups.archive_file(fd,dict(kind='plugins',path=str(home/'wordpress')))
            saved=backups.read_json(fd);saved['complete']=complete;backups.save_json(fd,saved)
            assert record['file']['bytes']>0
        finally:os.close(fd)
        with zipfile.ZipFile(home/'.masswpupdate'/location/'plugins.zip') as archive:
            assert archive.testzip() is None
            assert archive.read('plugins/example.php').startswith(b'<?php')
        return location
    wordpress=home/'wordpress';(wordpress/'wp-content').mkdir(parents=True)
    (wordpress/'wp-content/plugins').symlink_to(source,target_is_directory=True)
    # zip_tree opens its top directory without following symlinks.
    site1,site2='a'*32,'b'*32
    location=site1+'/2026/01/01/120000-'+'0'*32
    fd=backups.directory(str(home),location,True)
    backups.save_json(fd,dict(schema=1,location=location,complete=False,files=[],selected=['plugins.zip']))
    try:
        try:backups.archive_file(fd,dict(kind='plugins',path=str(wordpress)))
        except OSError:pass
        else:raise AssertionError('Followed a symlink backup source')
        assert set(os.listdir(fd))=={'manifest.json'}
    finally:os.close(fd)
    (wordpress/'wp-content/plugins').unlink();source.rename(wordpress/'wp-content/plugins')
    first=snapshot(site1,1);second=snapshot(site1,2);other=snapshot(site2,1)
    third=snapshot(site1,3)
    assert set(backups.prune(str(home),third,2)['removed'])=={location,first}
    assert (home/'.masswpupdate'/second/'plugins.zip').exists()
    failed=snapshot(site1,4,False)
    try:backups.prune(str(home),failed,1)
    except ValueError:pass
    else:raise AssertionError('Pruned before completing the new backup')
    assert (home/'.masswpupdate'/second/'plugins.zip').exists()
    fourth=snapshot(site1,5)
    assert set(backups.prune(str(home),fourth,1)['removed'])=={second,third,failed}
    assert (home/'.masswpupdate'/other/'plugins.zip').exists()
    sentinel=home/'sentinel';sentinel.write_text('preserve me');sentinel.chmod(0o600)
    target=home/'.masswpupdate'/fourth/'plugins.zip';original=target.read_bytes();target.unlink();target.symlink_to(sentinel)
    result=backups.delete_site(str(home),site1)
    assert not result['removed'] and result['warnings'] and sentinel.read_text()=='preserve me'
    target.unlink();os.link(sentinel,target)
    result=backups.delete_site(str(home),site1)
    assert not result['removed'] and result['warnings'] and sentinel.read_text()=='preserve me'
    target.unlink();target.write_bytes(original);target.chmod(0o600)
    assert backups.delete_site(str(home),site1)==dict(removed=[fourth],warnings=[])
    assert backups.delete_site(str(home),site1)==dict(removed=[],warnings=[])
    assert (home/'.masswpupdate'/other/'plugins.zip').exists()
    for path in (home/'.masswpupdate').rglob('*'):
        backups.owned(path.lstat(),path.is_dir())
    print('Unprivileged ZIP, retention, failure preservation, same-account isolation, symlink and hardlink checks passed')


if __name__=='__main__':run(sys.argv[1])
