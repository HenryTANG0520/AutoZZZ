"""Actual SDK metadata-only startup controller; network response is an isolated fixture."""
from dataclasses import replace
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import ROOT
from autozzz.onedragon_runtime import RuntimePaths
from autozzz.program_update_check import MetadataCheck


def main():
    platform.uname();paths=RuntimePaths.discover(ROOT,'cn');original=subprocess.Popen;starts=[];events=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-startup-sdk-') as directory:
        paths=replace(paths,data=Path(directory))
        def spawn(command,**options):
            if command[0]=='git':return original(command,**options)
            assert command[command.index('-m')+1]=='autozzz.onedragon_worker',command
            starts.append(command)
            return original([str(paths.python),str(ROOT/'tools/program_updates_sdk_smoke.py'),'--fixture-worker',
                '--source',str(paths.source),'--data',str(paths.data),'--bridge',str(ROOT)],**options)
        with patch('subprocess.Popen',side_effect=spawn):
            check=MetadataCheck(paths,'stable',events.append);check.worker.join(90)
            if check.alive:check.cancel();check.worker.join(60)
            assert not check.alive and check.process_exited,events[-1:]
        finished=events[-1];assert finished['state']=='success',finished
        assert len(starts)==1 and len(finished['catalog']['releases'])==1
        print(json.dumps(dict(actual_sdk=True,network_fixture=True,processes=len(starts),worker_exited=True,
            release=finished['catalog']['releases'][0]['version'],game_started=False,download_started=False,installation_started=False)))


if __name__=='__main__':main()
