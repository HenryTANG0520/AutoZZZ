"""Verify the real selected-index download and extraction; never run an installer."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading

project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
from autozzz.environment_tools import INDEXES,resolve_pinned_archive,read_network
from autozzz.gamepad_install import SHA256,extract_dependency,verify_dependency


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--index',choices=INDEXES,default=INDEXES[0]);parser.add_argument('--proxy')
    args=parser.parse_args();output=args.output.resolve()
    if output.exists():raise ValueError('请选择新的验证目录。')
    output.mkdir(parents=True);cancelled=threading.Event()
    address,hosts=resolve_pinned_archive(args.index,'vgamepad-0.1.0.tar.gz',SHA256,cancelled,proxy=args.proxy)
    raw=read_network(address,cancelled,proxy=args.proxy,allowed_hosts=hosts,limit=4*1024**2,seconds=180)
    assert hashlib.sha256(raw).hexdigest()==SHA256
    extract_dependency(raw,output/'dependency');verify_dependency(output/'dependency')
    result=dict(index=args.index,url=address,sha256=SHA256,bytes=len(raw),
        real_download=True,extracted_verified=True,driver_installed=False,game_input=False)
    (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
