"""Confine native resource edits to profile assets, retaining verifiable old versions."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import shutil
import threading
from unittest.mock import patch
import uuid

from .map_transaction import MapTransaction,fingerprint


def resource_name(value):
    if (type(value)is not str or not value or len(value)>120 or value!=value.strip()
        or value in ('.','..') or any(c in value for c in '/\\:<>"|?*') or any(ord(c)<32 for c in value)
        or value.endswith(('.', ' ')) or value.split('.')[0].upper() in
        {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}):
        raise ValueError('资源名称须为单个有效文件名，不可包含路径、保留字符或首尾空白。')
    return value


class ResourceArchives(MapTransaction):
    def __init__(self,root):
        self.root=Path(root).resolve();self.pending=self.root/'config/autozzz-devtools-transaction.json'
        self.edit_journal=self.root/'config/autozzz-devtools-edit.json'
        self.lock=threading.RLock();self.nested=False;self.internal=0;self.active=None;self.restoring=False

    def inside(self,path):
        path=Path(path).absolute()
        if path!=path.resolve() or path.exists() and path.is_file() and path.stat().st_nlink!=1:
            raise ValueError('开发工具资料路径被重定向或共享。')
        return super().inside(path)

    @staticmethod
    def _allowed(relative):
        p=tuple(x.casefold() for x in relative.parts)
        if len(p)<3:return False
        return (p[:2]==('assets','template') and len(p)>=5 and p[-1]in ('config.yml','raw.png','mask.png','features.xml')
                or p[:2]==('assets','image_analysis_pipelines') and len(p)==3 and relative.suffix.casefold()=='.yml'
                or p[:2]==('assets','image_analysis_templates') and len(p)==3 and relative.suffix.casefold()=='.npy'
                or p[:3]==('assets','game_data','screen_info') and len(p)==4 and relative.suffix.casefold()=='.yml'
                or p[0]=='plugins' and len(p)>=4 and 'screen_info' in p[2:-1] and relative.suffix.casefold()=='.yml')

    def archive_dir(self,identifier):
        if type(identifier)is not str or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('开发工具归档编号无效。')
        return self.inside(self.root/'archives/devtools'/identifier)

    def _put(self,path,raw):
        self.internal+=1
        try:return super()._put(path,raw)
        finally:self.internal-=1

    def edit_path(self,path):
        if self.active is None:raise ValueError('没有有效的开发资源修改事务。')
        path=self.target(self.inside(path).relative_to(self.root).as_posix())
        if path not in self.active['paths']:raise ValueError('源码尝试修改本次清单以外的资源：'+str(path.relative_to(self.root)))
        return path

    def publish_edit(self,writes):
        for path in writes:self.edit_path(path)
        original={p:self.read(p)for p in writes}
        if all(original[p]==raw for p,raw in writes.items()):return
        self.active['sequence']+=1;self.internal+=1
        try:
            return super().publish(writes,dict(action='native_write',internal=True,edit_id=self.active['id'],sequence=self.active['sequence']),
                lambda:all(fingerprint(self.read(p))==fingerprint(old)for p,old in original.items()))
        finally:self.internal-=1

    def mutate(self,paths,operation,label):
        """Each native write publishes known bytes; a durable outer journal groups recovery."""
        with self.lock:
            if self.nested:return operation()
            self.assert_clear()
            paths=list(dict.fromkeys(self.target(self.inside(p).relative_to(self.root).as_posix()) for p in paths))
            if not 1<=len(paths)<=1000:raise ValueError('开发工具本次修改文件数无效。')
            old=[self.read(p) for p in paths]
            if sum(len(x or b'')for x in old)>self.LIMIT//2:raise ValueError('开发工具本次修改资料过大。')
            identifier=uuid.uuid4().hex;folder=self.archive_dir(identifier);folder.mkdir(parents=True)
            for i,raw in enumerate(old):
                if raw is not None:self._put(folder/f'{i:04d}-old.bin',raw)
            metadata=dict(action=label,status='interrupted')
            entries=[dict(path=p.relative_to(self.root).as_posix(),old_hash=fingerprint(raw),new_hash='?') for p,raw in zip(paths,old)]
            manifest=dict(version=1,metadata=metadata,entries=entries)
            self._put(folder/'manifest.json',json.dumps(manifest,ensure_ascii=False).encode())
            self._put(self.edit_journal,json.dumps(dict(snapshot=identifier,state='editing',remaining=None)).encode())
            error=None;result=None;self.nested=True;self.active=dict(id=identifier,sequence=0,paths=set(paths))
            from .devtools_io import journal_io
            try:
                with journal_io(self):result=operation()
            except Exception as exc:error=exc
            finally:self.nested=False;self.active=None
            # BaseException/process death intentionally leave the outer journal for reviewed recovery.
            if self.pending.exists():
                if error:raise error
                raise RuntimeError('单文件发布尚未恢复；修改组与归档均已保留，请恢复中断修改。')
            for i,p in enumerate(paths):
                raw=self.read(p)
                if raw is not None:self._put(folder/f'{i:04d}-new.bin',raw)
                entries[i]['new_hash']=fingerprint(raw)
            metadata['status']='failed' if error else 'saved'
            if error:metadata['error']=str(error)
            self._put(folder/'manifest.json',json.dumps(manifest,ensure_ascii=False).encode())
            self.edit_journal.unlink()
            if error:raise error
            return result

    def list(self):
        folder=self.inside(self.root/'archives/devtools');result=[]
        if folder.exists():
            for p in sorted(folder.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
                try:
                    manifest,_,_=self.load(p.name)
                    if not manifest['metadata'].get('internal'):result.append(dict(id=p.name,metadata=manifest['metadata']))
                except (ValueError,OSError):continue
        return result

    def restore(self,identifier):
        manifest,files,_=self.load(identifier)
        def unchanged():return all(fingerprint(self.read(p))==fingerprint(new)for p,old,new in files)
        return self.publish({p:old for p,old,new in files},dict(action='restore',source=identifier),unchanged)

    def assert_clear(self):
        check=getattr(self,'checkpoint',None)
        if check is not None:check()
        if self.pending.exists():raise ValueError('上次开发工具单文件发布中断，请点击“恢复中断修改…”。')
        if self.active is None and not self.restoring and self.edit_journal.exists():raise ValueError('上次开发工具修改中断，请点击“恢复中断修改…”。')

    def _interrupted(self):
        raw=self.read(self.edit_journal)
        if raw is None:return None
        if len(raw)>128*1024:raise ValueError('开发工具中断清单过大。')
        marker=json.loads(raw)
        if (type(marker)is not dict or set(marker)!={'snapshot','state','remaining'}or marker['state']not in ('editing','recovering')
            or marker['remaining']is not None and (type(marker['remaining'])is not list or len(marker['remaining'])>1000)):
            raise ValueError('开发工具中断清单格式无效。')
        folder=self.archive_dir(marker['snapshot']);manifest_raw=self.archive_read(folder,'manifest.json')
        if manifest_raw is None or len(manifest_raw)>1024*1024:raise ValueError('中断归档清单缺失或过大。')
        manifest=json.loads(manifest_raw)
        if type(manifest)is not dict or set(manifest)!={'version','metadata','entries'}or manifest['version']!=1 or type(manifest['metadata'])is not dict:
            raise ValueError('中断归档格式无效。')
        original={};total=0
        if type(manifest['entries'])is not list or not 1<=len(manifest['entries'])<=1000:raise ValueError('中断归档条目无效。')
        for i,row in enumerate(manifest['entries']):
            if type(row)is not dict or set(row)!={'path','old_hash','new_hash'}:raise ValueError('中断归档条目无效。')
            path=self.target(row['path']);before=self.archive_read(folder,f'{i:04d}-old.bin');total+=len(before or b'')
            if path in original or fingerprint(before)!=row['old_hash']or total>self.LIMIT:raise ValueError('中断旧资料重复、被修改或过大。')
            original[path]=before
        if marker['remaining']is None:
            candidates=[]
            root=self.inside(self.root/'archives/devtools')
            for child in root.iterdir():
                if child.name==marker['snapshot']:continue
                try:other,files,_=self.load(child.name)
                except (ValueError,OSError):continue
                metadata=other['metadata']
                if metadata.get('internal')is True and metadata.get('edit_id')==marker['snapshot']:
                    sequence=metadata.get('sequence')
                    if type(sequence)is not int or not 1<=sequence<=1000:raise ValueError('中断写入序号无效。')
                    candidates.append((sequence,child.name))
            if len({sequence for sequence,_ in candidates})!=len(candidates):raise ValueError('中断写入序号重复。')
            marker['remaining']=[identifier for _,identifier in sorted(candidates,reverse=True)]
        layers=[]
        for identifier in marker['remaining']:
            metadata,files,_=self.load(identifier)
            if metadata['metadata'].get('edit_id')!=marker['snapshot']or metadata['metadata'].get('internal')is not True:
                raise ValueError('中断写入归档不属于本次修改。')
            if any(p not in original for p,_,_ in files):raise ValueError('中断写入超出原清单。')
            layers.append((identifier,files))
        return marker,manifest,original,layers,fingerprint(raw),fingerprint(manifest_raw)

    def interruption(self):
        """Read-only recovery preview. Unknown/external bytes are reported, never adopted."""
        state=self._interrupted();pending_raw=self.read(self.pending)
        if state is None:
            if pending_raw is None:return dict(pending=False,rows=[],conflicts=[])
            # Interrupted archive restoration uses the base single-transaction journal.
            marker=json.loads(pending_raw);manifest,files,digest=self.load(marker.get('id'))
            if marker.get('manifest_hash')!=digest:raise ValueError('单文件中断清单哈希不符。')
            rows=[];conflicts=[]
            for p,old,new in files:
                current=fingerprint(self.read(p));reason=''if current in (fingerprint(old),fingerprint(new))else'当前文件不匹配原版本或已发布版本'
                rows.append(dict(path=p.relative_to(self.root).as_posix(),old_hash=fingerprint(old),published_hash=fingerprint(new),current_hash=current,conflict=reason))
                if reason:conflicts.append(rows[-1]['path']+'：'+reason)
            return dict(pending=True,kind='single',journal_hash=fingerprint(pending_raw),rows=rows,conflicts=conflicts)
        marker,manifest,original,layers,journal_hash,manifest_hash=state
        current={p:self.read(p)for p in original};simulated=dict(current);conflicts={}
        all_layers=[]
        if pending_raw is not None:
            single=json.loads(pending_raw);_,files,digest=self.load(single.get('id'))
            if single.get('manifest_hash')!=digest:raise ValueError('中断单文件清单哈希不符。')
            if any(p not in original for p,_,_ in files):raise ValueError('中断单文件超出原清单。')
            all_layers.append(files)
        all_layers.extend(files for _,files in layers)
        published={}
        for files in all_layers:
            for p,old,new in files:
                published.setdefault(p,fingerprint(new))
                if fingerprint(simulated[p])not in (fingerprint(old),fingerprint(new)):
                    conflicts[p]='当前文件不匹配该事务已发布版本或尚未变更的原版本'
                else:simulated[p]=old
        rows=[]
        for p,old in original.items():
            if fingerprint(simulated[p])!=fingerprint(old)and p not in conflicts:conflicts[p]='缺少可核实的写入记录，不能确定当前内容来自本事务'
            rows.append(dict(path=p.relative_to(self.root).as_posix(),old_hash=fingerprint(old),published_hash=published.get(p,fingerprint(old)),current_hash=fingerprint(current[p]),conflict=conflicts.get(p,'')))
        return dict(pending=True,kind='edit',snapshot=marker['snapshot'],journal_hash=journal_hash,manifest_hash=manifest_hash,
            pending_hash=fingerprint(pending_raw),remaining=marker['remaining'],rows=rows,conflicts=[row['path']+'：'+row['conflict']for row in rows if row['conflict']])

    def recover_edit(self,review):
        with self.lock:
            current=self.interruption()
            if current!=review:raise ValueError('中断现场已变化，请刷新核对后重试。')
            if not current['pending']:return None
            if current['conflicts']:raise ValueError('中断现场存在外部修改或不确定内容，已保留：'+'；'.join(current['conflicts']))
            checkpoint=getattr(self,'checkpoint',None)
            if checkpoint is not None:checkpoint()
            if self.pending.exists():super().recover()
            if current['kind']=='single':return current
            marker,manifest,original,layers,_,_=self._interrupted()
            marker['state']='recovering';self._put(self.edit_journal,json.dumps(marker).encode());self.restoring=True
            try:
                for identifier,files in layers:
                    writes={p:old for p,old,new in files if fingerprint(self.read(p))!=fingerprint(old)}
                    if writes:
                        if any(fingerprint(self.read(p))not in (fingerprint(old),fingerprint(new))for p,old,new in files):raise ValueError('恢复时资源已变化。')
                        expected={p:fingerprint(self.read(p))for p in writes}
                        super().publish(writes,dict(action='recover_interrupted',source=identifier),lambda:all(fingerprint(self.read(p))==digest for p,digest in expected.items()))
                    marker['remaining'].remove(identifier);self._put(self.edit_journal,json.dumps(marker).encode())
                if any(fingerprint(self.read(p))!=fingerprint(old)for p,old in original.items()):raise ValueError('恢复后资料不匹配原版本；现场已保留。')
                folder=self.archive_dir(marker['snapshot'])
                for i,(p,old)in enumerate(original.items()):
                    self._put(folder/f'{i:04d}-new.bin',old);manifest['entries'][i]['new_hash']=fingerprint(old)
                manifest['metadata']['status']='recovered';self._put(folder/'manifest.json',json.dumps(manifest,ensure_ascii=False).encode())
                self.edit_journal.unlink()
                return current
            finally:self.restoring=False


class NativeResourceGuard:
    def __init__(self,ctx,root,checkpoint=None):
        self.ctx=ctx;self.tx=ResourceArchives(root);self.tx.checkpoint=checkpoint;self.stack=ExitStack()

    def template_dir(self,sub_dir,template_id):
        resource_name(sub_dir);resource_name(template_id)
        return self.tx.inside(self.tx.root/'assets/template'/sub_dir/template_id)

    def template_paths(self,template):
        paths=[]
        for sub,identifier in ((template.sub_dir,template.template_id),(template.old_sub_dir,template.old_template_id)):
            if not sub or not identifier:continue
            directory=self.template_dir(sub,identifier)
            if directory.exists():
                for file in directory.iterdir():
                    if not file.is_file() or file.name not in ('config.yml','raw.png','mask.png','features.xml'):
                        raise ValueError('模板目录含未知文件，无法安全改名或删除。')
                    self.tx.inside(file)
            paths.extend(directory/name for name in ('config.yml','raw.png','mask.png','features.xml'))
        return paths

    def screen_paths(self,screen=None,identifier=None):
        loader=self.ctx.screen_loader;paths=[loader.merge_yml_file_path]
        for row in loader.screen_info_list:
            resource_name(row.screen_id);paths.append(loader.get_yml_file_path(row.screen_id))
        if screen is not None:
            for key in (screen.screen_id,screen.old_screen_id):
                if key:resource_name(key);paths.append(loader.get_yml_file_path(key))
        if identifier is not None:resource_name(identifier);paths.append(loader.get_yml_file_path(identifier))
        paths.extend(loader._extra_screen_file_path_map.values())
        # Separate plugin resources are allowed only inside this profile's plugin screen_info tree.
        return [self.tx.target(self.tx.inside(p).relative_to(self.tx.root).as_posix()) for p in paths]

    def __enter__(self):
        from one_dragon.base.screen.template_info import TemplateInfo
        import one_dragon.base.screen.template_info as module
        original_dir=module.get_template_dir_path
        def directory(sub_dir,template_id,make_dir=False):
            if make_dir:self.template_dir(sub_dir,template_id)
            else:
                for value in (sub_dir,template_id):
                    if value:resource_name(value)
                self.tx.inside(self.tx.root/'assets/template'/sub_dir/template_id)
            return original_dir(sub_dir,template_id,make_dir)
        # Empty in-memory templates are supported by upstream; require names only when persisted.
        for method in ('save_config','save_raw','save_mask','make_template_dir'):
            original=getattr(TemplateInfo,method)
            def invoke(template,*a,_method=method,_original=original,**kw):
                self.template_dir(template.sub_dir,template.template_id)
                return self.tx.mutate(self.template_paths(template),lambda:_original(template,*a,**kw),'template_'+_method)
            self.stack.enter_context(patch.object(TemplateInfo,method,invoke))
        original_delete=TemplateInfo.delete
        def delete(template):
            target=self.template_dir(template.sub_dir,template.template_id)
            def remove():
                original_delete(template)
                if target.exists():shutil.rmtree(target)
            return self.tx.mutate(self.template_paths(template),remove,'template_delete')
        self.stack.enter_context(patch.object(TemplateInfo,'delete',delete))
        # Validate dynamic paths used by make_template_dir, including old paths in source moves.
        self.stack.enter_context(patch.object(module,'get_template_dir_path',directory))
        service=self.ctx.cv_service
        for name,folder,suffix in [('pipeline',service.PIPELINE_DIR,'.yml'),('template_contour',service.TEMPLATE_DIR,'.npy')]:
            for verb in ('save','delete','rename','load'):
                method=verb+'_'+name;original=getattr(service,method)
                def invoke(*args,_verb=verb,_method=method,_original=original,_folder=folder,_suffix=suffix,**kw):
                    count=2 if _verb=='rename' else 1
                    paths=[self.tx.inside(Path(_folder)/(resource_name(value)+_suffix)) for value in args[:count]]
                    if _verb=='load':return _original(*args,**kw)
                    return self.tx.mutate(paths,lambda:_original(*args,**kw),_method)
                self.stack.enter_context(patch.object(service,method,invoke))
        loader=self.ctx.screen_loader
        for method in ('save_screen','delete_screen','save'):
            original=getattr(loader,method)
            def invoke(*args,_method=method,_original=original,**kw):
                screen=args[0]if args and _method=='save_screen'else None
                identifier=(args[0]if args else kw.get('screen_id')) if _method!='save_screen' else None
                return self.tx.mutate(self.screen_paths(screen,identifier),lambda:_original(*args,**kw),'screen_'+_method)
            self.stack.enter_context(patch.object(loader,method,invoke))
        return self

    def __exit__(self,*args):return self.stack.__exit__(*args)
