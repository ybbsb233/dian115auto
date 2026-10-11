import unittest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from deploy import linux
from deploy.linux import render_unit, unit_quote


class LinuxServiceTests(unittest.TestCase):
    def test_ssh_login_uses_virtual_display_and_requires_credentials(self):
        with patch.object(linux.sys, 'platform', 'linux'), \
             patch.object(linux.os, 'geteuid', return_value=1000, create=True), \
             patch.object(linux, 'validate', return_value={}), \
             patch.object(linux, 'xvfb_command', return_value=['/usr/bin/xvfb-run', '-a']), \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(linux, 'run') as run:
            self.assertEqual(linux.main(['login']), 0)
            self.assertIn('DIAN115_EMAIL', run.call_args_list[0].args[2])
            self.assertEqual(run.call_args_list[-1].args[:2], ('/usr/bin/xvfb-run', '-a'))
            self.assertIn('login', run.call_args_list[-1].args)
            self.assertFalse(any('serve' in call.args for call in run.call_args_list))

    def test_install_service_enables_without_starting_or_signing_in(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'data').mkdir()
            (root / 'data/account.json').write_text(json.dumps({
                'account_id': '1', 'profile': str((root / '.state/browser').resolve())}), encoding='utf-8')
            with patch.object(linux.sys, 'platform', 'linux'), \
                 patch.object(linux.os, 'geteuid', return_value=1000, create=True), \
                 patch.object(linux, 'ROOT', root), \
                 patch.object(linux, 'validate', return_value={}), \
                 patch.object(linux, 'run') as run, \
                 patch.object(linux.subprocess, 'run'), \
                 patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')}):
                self.assertEqual(linux.main(['install-service', '--display', 'headless', '--config', str(root / 'config.toml')]), 0)
                self.assertTrue((root / 'config/systemd/user/dian115auto.service').is_file())
                self.assertEqual(run.call_args_list[-1].args, ('systemctl', '--user', 'enable', linux.UNIT))
                self.assertFalse(any('start' in call.args or 'login' in call.args for call in run.call_args_list))

    def test_setup_installs_dependencies_without_running_application(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(linux.sys, 'platform', 'linux'), \
                 patch.object(linux.os, 'geteuid', return_value=1000, create=True), \
                 patch.object(linux, 'ROOT', root), \
                 patch.object(linux, 'PYTHON', root / '.venv/bin/python'), \
                 patch.object(linux, 'validate', return_value={}), \
                 patch.object(linux, 'ensure_xvfb'), \
                 patch.object(linux, 'run') as run:
                self.assertEqual(linux.main(['setup']), 0)
                self.assertEqual(run.call_count, 3)
                self.assertIn('--with-deps', run.call_args_list[-1].args)
                self.assertFalse(any('serve' in call.args or 'login' in call.args for call in run.call_args_list))

    def test_service_quotes_paths_and_does_not_embed_user_identity(self):
        text = render_unit(Path('/home/alice/my project%$'), Path('/home/alice/config.toml'),
                           Path('/home/alice/.config/dian115auto/api.env'))
        self.assertIn('WorkingDirectory="/home/alice/my project%%$"', text)
        self.assertIn('"/home/alice/my project%%$$/.venv/bin/python"', text)
        self.assertIn('"serve" "--config" "/home/alice/config.toml"', text)
        self.assertIn('WantedBy=default.target', text)
        self.assertNotIn('User=', text)

    def test_visible_browser_service_preserves_display_environment(self):
        text = render_unit('/app', '/app/config.toml', '/home/user/api.env',
                           {'DISPLAY': ':2', 'XAUTHORITY': '/home/user/.Xauthority'})
        self.assertIn('Environment="DISPLAY=:2"', text)
        self.assertIn('Environment="XAUTHORITY=/home/user/.Xauthority"', text)

    def test_rejects_unit_directive_injection(self):
        with self.assertRaises(ValueError):
            unit_quote('/app\nExecStart=/bin/unwanted')

    def test_virtual_display_service_forces_headed_browser(self):
        text = render_unit('/app', '/app/config.toml', '/home/user/api.env', virtual_display='/usr/bin/xvfb-run')
        self.assertIn('ExecStart="/usr/bin/xvfb-run" "-a"', text)
        self.assertIn('-nolisten tcp', text)
        self.assertIn('"--headed"', text)
        self.assertNotIn('Environment="DISPLAY=', text)

    def test_auto_display_uses_virtual_display_without_desktop(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(linux.display_mode('auto'), 'xvfb')
        with patch.dict(os.environ, {'DISPLAY': ':2'}):
            self.assertEqual(linux.display_mode('auto'), 'desktop')
