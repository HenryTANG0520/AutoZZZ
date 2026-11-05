import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from autozzz.onedragon_startup import game_processes
from autozzz.process_inventory import process_inventory


class ProcessInventoryTests(unittest.TestCase):
    def test_matches_current_process_and_creation_time_without_psutil(self):
        name=Path(sys.executable).name
        with patch.dict(sys.modules,{'psutil':None}):
            own=process_inventory(name.upper(),only_pid=os.getpid())
            self.assertEqual(len(own),1)
            self.assertEqual(own[0]['pid'],os.getpid())
            self.assertEqual(Path(own[0]['path']).name.casefold(),name.casefold())
            self.assertGreater(own[0]['created'],0)
            self.assertEqual(process_inventory('AutoZZZ-Test-No-Match.exe',only_pid=os.getpid()),[])
            with patch('autozzz.process_inventory.process_inventory',return_value=[]) as inventory:
                self.assertEqual(game_processes(),[])
                inventory.assert_called_once_with('ZenlessZoneZero.exe')
