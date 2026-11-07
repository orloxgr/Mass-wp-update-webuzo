"""Account-owned backups. Every filesystem operation here runs without root."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import account_task

FILES = {'plugins.zip','themes.zip','database.zip'}
SNAPSHOT = re.compile(r'[0-9a-f]{32}/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{6}-[0-9a-f]{32}\Z')


def owned(info, directory=False):
    if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise ValueError('Backup storage must be owned by this account with directory mode 700 and file mode 600')


def child(parent,name,create=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in ('.','..'): raise ValueError('Invalid backup component')
    if create:
        try: os.mkdir(name,0o700,dir_fd=parent)
        except FileExistsError: pass
    fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    try: owned(os.fstat(fd),True)
    except Exception: os.close(fd);raise
    return fd


def open_parts(home,parts,create=False):
    fd=os.open(home,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in parts:
            following=child(fd,name,create);os.close(fd);fd=following
        return fd
    except Exception: os.close(fd);raise


def directory(home,location,create=False):
    if not SNAPSHOT.fullmatch(location): raise ValueError('Invalid backup location')
    return open_parts(home,['.masswpupdate',*location.split('/')],create)


def read_json(fd,name='manifest.json'):
    source=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    with os.fdopen(source,'rb') as stream:
        owned(os.fstat(stream.fileno()))
        raw=stream.read(65537)
        if len(raw)>65536: raise ValueError('Invalid backup manifest size')
        return json.loads(raw)


def save_json(fd,value):
    name='.manifest-'+secrets.token_hex(16)+'.part'
    handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write((json.dumps(value,sort_keys=True)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        os.replace(name,'manifest.json',src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try:os.unlink(name,dir_fd=fd)
        except FileNotFoundError:pass


def digest(fd,name):
    source=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    with os.fdopen(source,'rb') as stream:
        info=os.fstat(stream.fileno());owned(info);value=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return dict(name=name,bytes=info.st_size,sha256=value.hexdigest())


def capacity(fd):
    space=os.fstatvfs(fd);limit=space.f_bavail*space.f_frsize-256*1024*1024
    if limit<=0:raise ValueError('Insufficient free space for backup')
    soft,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    if hard!=resource.RLIM_INFINITY:limit=min(limit,hard)
    resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit))


def zip_tree(archive,source,kind):
    top=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for folder,dirs,files,fd in os.fwalk('.',dir_fd=top,follow_symlinks=False):
            prefix=kind+'/'+('' if folder=='.' else folder[2:]+'/')
            archive.writestr(prefix,b'')
            for name in [*dirs,*files]:
                info=os.stat(name,dir_fd=fd,follow_symlinks=False);entry=prefix+name
                if stat.S_ISLNK(info.st_mode):
                    item=zipfile.ZipInfo(entry);item.create_system=3;item.external_attr=(stat.S_IFLNK|0o777)<<16
                    archive.writestr(item,os.readlink(name,dir_fd=fd))
                elif stat.S_ISDIR(info.st_mode):continue
                elif stat.S_ISREG(info.st_mode):
                    handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    with os.fdopen(handle,'rb') as data,archive.open(entry,'w',force_zip64=True) as output:
                        if not stat.S_ISREG(os.fstat(data.fileno()).st_mode):raise ValueError('Backup source changed')
                        shutil.copyfileobj(data,output,1024*1024)
                else:raise ValueError('Backup source contains a special file')
    finally:os.close(top)


def zip_database(archive,request):
    args=[request['php'],request['wp'],'--path='+request['path'],'--skip-plugins','--skip-themes','--skip-packages','--no-color','db','export','-','--quiet']
    if request.get('socket'):args.append('--socket='+request['socket'])
    errors=bytearray()
    with subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        try:
            with archive.open('database.sql','w',force_zip64=True) as output,selectors.DefaultSelector() as selector:
                selector.register(process.stdout,selectors.EVENT_READ,'data');selector.register(process.stderr,selectors.EVENT_READ,'error')
                count=0
                while selector.get_map():
                    for key,unused in selector.select():
                        data=os.read(key.fileobj.fileno(),65536)
                        if not data:selector.unregister(key.fileobj);continue
                        if key.data=='data':output.write(data);count+=len(data)
                        else:
                            errors.extend(data)
                            if len(errors)>4*1024*1024:raise ValueError('Database export error output exceeded 4 MiB')
            if process.wait()!=0:raise ValueError('Database export failed: '+errors.decode(errors='replace')[-3000:])
            if not count:raise ValueError('Database export is empty')
        except Exception:
            process.kill();process.wait();raise


def archive_file(fd,request):
    kind=request['kind'];name=kind+'.zip'
    if name not in FILES:raise ValueError('Invalid backup type')
    saved=read_json(fd)
    if saved.get('complete') or name in [f['name'] for f in saved['files']]:raise ValueError('Backup already exists')
    temporary='.'+name+'-'+secrets.token_hex(16)+'.part'
    handle=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        capacity(handle)
        with os.fdopen(handle,'w+b') as output:
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
                if kind=='database':zip_database(archive,request)
                else:zip_tree(archive,Path(request['path'])/'wp-content'/kind,kind)
            output.flush();os.fsync(output.fileno())
        # Same snapshot/file is never overwritten, including malicious links.
        os.link(temporary,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
        os.unlink(temporary,dir_fd=fd)
        record=digest(fd,name);saved['files'].append(record);save_json(fd,saved)
        return dict(file=record,manifest=digest(fd,'manifest.json'))
    finally:
        try:os.unlink(temporary,dir_fd=fd)
        except FileNotFoundError:pass


def snapshots(home,site):
    base=os.open(home,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        root=child(base,'.masswpupdate');os.close(base);base=root
        root=child(base,site);os.close(base);base=root
        found=[]
        def walk(fd,prefix,depth):
            pattern=('[0-9]{4}','[0-9]{2}','[0-9]{2}','[0-9]{6}-[0-9a-f]{32}')[depth]
            for name in os.listdir(fd):
                if not re.fullmatch(pattern,name):continue
                nested=child(fd,name)
                try:
                    relative=prefix+'/'+name
                    if depth==3:found.append(relative)
                    else:walk(nested,relative,depth+1)
                finally:os.close(nested)
        walk(base,site,0);return sorted(found,reverse=True)
    finally:os.close(base)


def remove_snapshot(home,location):
    fd=directory(home,location)
    try:
        saved=read_json(fd)
        if saved.get('schema')!=1 or saved.get('location')!=location:raise ValueError('Unrecognized backup manifest')
        names=set(os.listdir(fd));allowed={f['name'] for f in saved['files']}|{'manifest.json'}
        if names!=allowed or not allowed <= FILES|{'manifest.json'}:raise ValueError('Snapshot contains unrecognized files')
        for name in names:owned(os.stat(name,dir_fd=fd,follow_symlinks=False))
        for name in names:os.unlink(name,dir_fd=fd)
    finally:os.close(fd)
    parts=location.split('/')
    parent_fd=open_parts(home,['.masswpupdate',*parts[:-1]])
    try:os.rmdir(parts[-1],dir_fd=parent_fd)
    finally:os.close(parent_fd)


def delete_site(home,site):
    try:locations=snapshots(home,site)
    except FileNotFoundError:return dict(removed=[],warnings=[])
    removed=[];warnings=[]
    for location in locations:
        try:remove_snapshot(home,location);removed.append(location)
        except FileNotFoundError:continue
        except Exception as error:warnings.append(location+': '+str(error))
    return dict(removed=removed,warnings=warnings)


def prune(home,location,keep):
    if type(keep) is not int or not 1<=keep<=10:raise ValueError('Choose 1 to 10 backups to keep')
    current=directory(home,location)
    try:
        if read_json(current).get('complete') is not True:raise ValueError('Complete the new backup before pruning')
    finally:os.close(current)
    removed=[];warnings=[];retained=1
    for previous in snapshots(home,location.split('/')[0]):
        if previous==location:continue
        try:
            fd=directory(home,previous)
            try:
                saved=read_json(fd)
                if saved.get('schema')!=1 or saved.get('location')!=previous:raise ValueError('Unrecognized backup manifest')
                if saved.get('complete') is True and retained<keep:retained+=1;continue
            finally:os.close(fd)
            remove_snapshot(home,previous)
            removed.append(previous)
        except FileNotFoundError:
            continue
        except Exception as error:warnings.append(str(error));continue
    return dict(removed=removed,warnings=warnings)


def main():
    operation,home,raw=sys.argv[1:4];request=json.loads(raw)
    unix=pwd.getpwuid(os.geteuid())
    if unix.pw_uid==0 or unix.pw_name=='webuzo' or unix.pw_dir!=home:raise ValueError('A non-root hosting account is required')
    site=hashlib.sha256((unix.pw_name+'\0'+request['path']).encode()).hexdigest()[:32]
    if operation=='delete_site':return delete_site(home,site)
    location=request['location']
    if not SNAPSHOT.fullmatch(location) or location.split('/')[0]!=site:raise ValueError('Backup does not belong to this WordPress installation')
    if operation in ('prepare','archive'):account_task.check(home,request['path'])
    if operation=='prune':return prune(home,location,request['keep'])
    fd=directory(home,location,create=operation=='prepare')
    try:
        if operation=='prepare':
            try:os.stat('manifest.json',dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise ValueError('Backup snapshot already exists')
            save_json(fd,dict(schema=1,location=location,complete=False,files=[],selected=request['selected']))
            return dict(ok=True)
        if operation=='archive':return archive_file(fd,request)
        if operation=='commit':
            saved=read_json(fd)
            if set(f['name'] for f in saved['files'])!=set(saved['selected']):raise ValueError('Backup set is incomplete')
            saved['complete']=True;save_json(fd,saved)
            return dict(manifest=digest(fd,'manifest.json'))
        if operation=='read':
            name=request['name']
            if name not in FILES|{'manifest.json'}:raise ValueError('Invalid backup file')
            source=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            with os.fdopen(source,'rb') as stream:
                info=os.fstat(stream.fileno());owned(info)
                if info.st_size!=request['bytes']:raise ValueError('Backup size changed since creation')
                shutil.copyfileobj(stream,sys.stdout.buffer,1024*1024)
            return None
        raise ValueError('Unknown backup operation')
    finally:os.close(fd)


if __name__=='__main__':
    try:
        result=main()
        if result is not None:print(json.dumps(result))
    except Exception as error:
        print(str(error),file=sys.stderr);raise SystemExit(1)
