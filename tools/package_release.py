"""生成完整包清单和发布 ZIP；不上传或修改 GitHub Release。"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.program_package import MANIFEST,create_manifest,verify_package,read_manifest,preserve_user_files
from autozzz.onedragon_bundle import filesystem_path,digest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True);parser.add_argument('--version');parser.add_argument('--output',type=Path);parser.add_argument('--base-commit');parser.add_argument('--preserve-from',type=Path);parser.add_argument('--reindex',action='store_true')
    args=parser.parse_args()
    if args.preserve_from is not None:
        print({'preserved_user_files':preserve_user_files(args.preserve_from,args.package)});return
    if args.version is None:parser.error('生成发布清单需要 --version。')
    commit=args.base_commit or (read_manifest(args.package)['base_commit'] if (args.package/MANIFEST).exists() else
        subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[1]),'rev-parse','HEAD'],text=True).strip())
    data=create_manifest(args.package,args.version,commit,reindex=args.reindex)
    if args.output is None:print({'version':data['version'],'files':len(data['files'])});return
    if args.output.exists() or args.output.with_suffix(args.output.suffix+'.sha256').exists():raise ValueError('发布 ZIP 或摘要已存在。')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
        for name in sorted(list(data['files'])+[MANIFEST]):archive.write(filesystem_path(args.package/name),'AutoZZZ/'+name)
    verify_package(args.package)
    if args.output.stat().st_size>4*1024**3:raise ValueError('发布 ZIP 超过 4 GB，不能作为自动更新资产。')
    checksum=digest(args.output)
    args.output.with_suffix(args.output.suffix+'.sha256').write_text(checksum+'  '+args.output.name+'\n',encoding='utf-8')
    print({'version':data['version'],'sha256':checksum,'files':len(data['files']),'output':str(args.output)})


if __name__=='__main__':main()
