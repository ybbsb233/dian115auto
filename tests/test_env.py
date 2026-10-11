import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from settings import load_settings


class EnvTests(unittest.TestCase):
    def test_env_file_relative_to_config_and_system_environment_takes_precedence(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config.toml').write_text('', encoding='utf-8')
            (root / '.env').write_text("DIAN115_EMAIL=file@example.com\nDIAN115_PASSWORD='value#with space'\n", encoding='utf-8')
            with patch.dict(os.environ, {'DIAN115_EMAIL':'system@example.com'}, clear=True):
                cfg = load_settings(root / 'config.toml')
                self.assertEqual(os.environ['DIAN115_EMAIL'], 'system@example.com')
                self.assertEqual(os.environ['DIAN115_PASSWORD'], 'value#with space')
                self.assertNotIn('value#with space', repr(cfg))
