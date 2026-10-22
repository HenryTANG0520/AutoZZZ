import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from autozzz.config import Settings
from autozzz.__main__ import main


class CliContractTests(unittest.TestCase):
    def test_check_and_launch_work_without_daily_selection_and_do_not_save(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.json'
            Settings(scratch=False, coffee=False, video=False, daily=False).save(config)
            original = config.read_bytes()
            with patch('autozzz.app.inspect_game', return_value={'mock': True}) as inspect, \
                    patch('autozzz.app.Runner') as runner, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                runner.return_value.enter_game.return_value = {'status': 'success'}
                self.assertEqual(main(['--check', '--config', str(config)]), 0)
                self.assertEqual(main(['--launch', '--config', str(config)]), 0)
                inspect.assert_called_once()
                runner.return_value.enter_game.assert_called_once()
            self.assertEqual(config.read_bytes(), original)
