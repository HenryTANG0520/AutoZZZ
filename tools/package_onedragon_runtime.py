"""从已安装的固定 SDK 构建可移动运行时；不安装驱动、不复制账号或运行资料。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_bundle import MANIFEST_NAME,digest,relative_name,tree_files,verify_bundle,filesystem_path
from autozzz.onedragon_runtime import RuntimePaths,UPSTREAM_REVISION,PROTOCOL_VERSION


def run(command):
    return subprocess.run(command,check=True,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def tracked(root,*folders):
    names=run(['git','-C',str(root),'ls-files','-z','--',*folders]).stdout.decode('utf-8').split('\0')
    return [relative_name(name) for name in names if name]


def copy_files(source,target,names):
    for name in names:
        origin=filesystem_path(source/name)
        if not origin.is_file() or origin.is_symlink() or getattr(origin.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('不能打包缺失或链接的源文件：'+name)
        destination=filesystem_path(target/name);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(origin,destination)


def build(output: Path):
    root=Path(__file__).resolve().parents[1]
    defaults=RuntimePaths.discover(root)
    if defaults.bundle is not None:raise ValueError('请在源码工作目录构建，不重打包已有分发运行时。')
    defaults.verify()
    # 源码与内置资料必须保持完整固定版本，包括将随包提供的许可证与部署声明。
    run(['git','-C',str(defaults.source),'diff','--quiet','HEAD','--','.'])
    facts=json.loads(run([str(defaults.python),'-s','-P','-c',
        'import sys,site,json,importlib.metadata as m; print(json.dumps(dict(version=sys.version.split()[0],'
        'base=sys.base_prefix,site=site.getsitepackages()[-1],'
        'packages=sorted((d.metadata["Name"],d.version) for d in m.distributions()))))']).stdout.decode('utf-8'))
    if not facts['version'].startswith('3.11.'):raise ValueError('需要已安装的独立 Python 3.11 SDK。')
    base,packages=Path(facts['base']).resolve(),Path(facts['site']).resolve()
    output=output.resolve()
    if output.exists():raise ValueError('输出目录已存在，请使用新的构建目录。')
    for source in (defaults.source,base,packages):
        if output==source or output in source.parents or source in output.parents:
            raise ValueError('输出目录不能覆盖或嵌入源 SDK / Python 环境。')
    started=time.monotonic();output.mkdir(parents=True)
    print('复制固定版本上游源码及内置资源。',flush=True)
    source_files=tracked(defaults.source)
    copy_files(defaults.source,output/'source',source_files)
    print('复制可移动 Python 3.11 和实际安装依赖。',flush=True)
    ignored=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo')
    base_filesystem=filesystem_path(base)
    def base_ignore(folder,names):
        skip=ignored(folder,names)
        if Path(folder)==base_filesystem/'Lib':skip=set(skip)|{'site-packages'}
        return skip
    shutil.copytree(base_filesystem,filesystem_path(output/'python'),ignore=base_ignore)
    shutil.copytree(filesystem_path(packages),filesystem_path(output/'python/Lib/site-packages'),ignore=ignored)
    print('复制桥接代码与许可证；不复制用户配置、模型或报告。',flush=True)
    bridge_files=tracked(root,'assets','LICENSE','LICENSE_NOTICE.md','launcher.py','requirements.txt','requirements-dev.txt','setup.ps1')
    bridge_files += ['tools/build.ps1','tools/package_onedragon_runtime.py','tools/setup_onedragon.ps1','tools/package_release.py']
    bridge_files += sorted(path.relative_to(root).as_posix() for path in (root/'autozzz').rglob('*.py') if '__pycache__' not in path.parts)
    copy_files(root,output/'bridge',bridge_files)
    files={name:digest(path) for name,path in sorted(tree_files(output))}
    metadata=dict(format=1,revision=UPSTREAM_REVISION,protocol=PROTOCOL_VERSION,python_version=facts['version'],
                  files=files,resources=[name for name in source_files if name.startswith(('config/','assets/'))],
                  main_revision=run(['git','-C',str(root),'rev-parse','HEAD']).stdout.decode().strip(),
                  bridge_has_worktree_changes=bool(run(['git','-C',str(root),'status','--porcelain','--','autozzz','assets']).stdout),
                  packages=facts['packages'])
    marker=output/MANIFEST_NAME
    temporary=output/(MANIFEST_NAME+'.tmp');temporary.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(marker)
    verify_bundle(output,UPSTREAM_REVISION,PROTOCOL_VERSION)
    result=dict(output=str(output),files=len(files),bytes=sum(path.stat().st_size for _,path in tree_files(output)),
                python_version=facts['version'],revision=UPSTREAM_REVISION,seconds=round(time.monotonic()-started,2),
                user_data_copied=False,virtual_driver_installed=False)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)


if __name__=='__main__':main()
