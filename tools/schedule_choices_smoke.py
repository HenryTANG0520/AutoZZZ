"""Read actual SDK account/application names in isolated data; no game initialization."""
from pathlib import Path
import json
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import ROOT
from autozzz.schedules import Schedule
from autozzz.schedules_ui import native_query
from autozzz.onedragon_runtime import RuntimePaths


def main():
    paths=RuntimePaths.discover(ROOT,'cn')
    with tempfile.TemporaryDirectory(prefix='autozzz-schedule-choices-') as directory:
        isolated=RuntimePaths(paths.source,paths.python,Path(directory))
        with patch('autozzz.onedragon_runtime.RuntimePaths.discover',return_value=isolated):
            value=native_query(Schedule(mode='native',instance=1),directory=True)
        assert value['catalog']['ready_for_game'] is False
        assert all(type(row['name']) is str for row in value['accounts']['instances'])
        assert all(type(row['name']) is str for row in value['catalog']['applications'])
        print(json.dumps(dict(accounts=len(value['accounts']['instances']),applications=len(value['catalog']['applications']),
            game_initialized=False,game_started=False,worker_exited=True)))


if __name__=='__main__':main()
