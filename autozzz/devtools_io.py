"""Buffer native resource writes and publish known bytes through the transaction journal."""
import builtins
from contextlib import ExitStack,contextmanager
import io
import os
from pathlib import Path
import shutil
from unittest.mock import patch


@contextmanager
def journal_io(tx):
    original_open=builtins.open;original_io_open=io.open
    original_rename=os.rename;original_replace=os.replace;original_unlink=os.unlink;original_remove=os.remove
    original_move=shutil.move;original_rmtree=shutil.rmtree
    def opened(original,file,mode='r',*args,**kwargs):
        if tx.internal or not any(c in mode for c in 'wax+'):return original(file,mode,*args,**kwargs)
        if mode not in ('w','wt','wb'):raise ValueError('开发资源仅允许完整文件写入。')
        path=tx.edit_path(file);binary='b'in mode
        encoding=kwargs.get('encoding') or (args[1] if len(args)>1 else None) or 'utf-8'
        class Buffer(io.BytesIO if binary else io.StringIO):
            aborted=False
            def __exit__(self,kind,error,trace):
                self.aborted=kind is not None;self.close();return False
            def close(self):
                if not self.closed:
                    raw=self.getvalue()
                    if not self.aborted:tx.publish_edit({path:raw if binary else raw.encode(encoding)})
                super().close()
            def __del__(self):
                # Native writers use with/close. An abandoned buffer must not publish later.
                self.aborted=True
                super().close()
        buffer=Buffer();buffer.name=str(path);return buffer
    def remove(original,path,*args,**kwargs):
        if tx.internal:return original(path,*args,**kwargs)
        if args or set(kwargs)-{'dir_fd'} or kwargs.get('dir_fd')is not None:raise ValueError('开发资源删除参数无效。')
        target=tx.edit_path(path)
        if not target.exists():raise FileNotFoundError(str(target))
        tx.publish_edit({target:None})
    def move(original,source,destination,*args,**kwargs):
        if tx.internal:return original(source,destination,*args,**kwargs)
        if args or kwargs:raise ValueError('开发资源移动参数无效。')
        source,destination=tx.edit_path(source),tx.edit_path(destination)
        if source==destination:return str(destination)
        raw=tx.read(source)
        if raw is None:raise FileNotFoundError(str(source))
        tx.publish_edit({source:None,destination:raw});return str(destination)
    def rmtree(path,*args,**kwargs):
        if tx.internal:return original_rmtree(path,*args,**kwargs)
        if args or kwargs:raise ValueError('开发资源目录删除参数无效。')
        directory=tx.inside(path)
        if not directory.is_dir():raise FileNotFoundError(str(directory))
        files=[]
        for file in directory.iterdir():
            if not file.is_file():raise ValueError('模板目录含非文件，拒绝递归删除。')
            files.append(tx.edit_path(file))
        if files:tx.publish_edit({file:None for file in files})
        directory.rmdir()
    with ExitStack()as stack:
        stack.enter_context(patch.object(builtins,'open',lambda *a,**kw:opened(original_open,*a,**kw)))
        stack.enter_context(patch.object(io,'open',lambda *a,**kw:opened(original_io_open,*a,**kw)))
        stack.enter_context(patch.object(os,'unlink',lambda *a,**kw:remove(original_unlink,*a,**kw)))
        stack.enter_context(patch.object(os,'remove',lambda *a,**kw:remove(original_remove,*a,**kw)))
        stack.enter_context(patch.object(os,'rename',lambda *a,**kw:move(original_rename,*a,**kw)))
        stack.enter_context(patch.object(os,'replace',lambda *a,**kw:move(original_replace,*a,**kw)))
        stack.enter_context(patch.object(shutil,'move',lambda *a,**kw:move(original_move,*a,**kw)))
        stack.enter_context(patch.object(shutil,'rmtree',rmtree))
        try:
            from one_dragon.utils import cv2_utils
        except ImportError:cv2_utils=None
        if cv2_utils is not None:
            def save_image(image,file):
                import cv2
                path=tx.edit_path(file)
                if image is None:raise ValueError('模板图片为空。')
                raw_image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)if image.ndim==3 else image
                ok,encoded=cv2.imencode(path.suffix,raw_image)
                if not ok:raise ValueError('模板图片编码失败。')
                tx.publish_edit({path:encoded.tobytes()})
            stack.enter_context(patch.object(cv2_utils,'save_image',save_image))
        yield
