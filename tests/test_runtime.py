from dataclasses import replace
from datetime import datetime, time, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import main
from runtime import Engine, Scheduler, execute_safely
from playwright.sync_api import Error as PlaywrightError
from settings import Settings, load_settings
from storage import atomic_json


class FakeBrowser:
    def __init__(self, result=None):
        self.result = result or {"status": "success", "points_change": 0, "balance": 10, "mode": "lucky"}
        self.calls = []

    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return dict(self.result)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cfg = Settings(data=root / "data", profile=root / "profile", zone=ZoneInfo("Asia/Shanghai"), at=time(9))
        self.now = datetime(2026, 10, 11, 9, tzinfo=self.cfg.zone)
        self.browser = FakeBrowser()
        self.engine = Engine(self.cfg, "123", self.browser, lambda: self.now)

    def test_status_is_read_only_and_missing_is_unknown(self):
        self.assertEqual(self.engine.status()["status"], "unknown")
        self.assertEqual(self.browser.calls, [])
        self.assertFalse(self.cfg.data.exists())

    def test_browser_spawn_error_is_explicit_and_sanitized(self):
        with patch.object(self.browser, "execute", side_effect=PlaywrightError(
                "BrowserType.launch_persistent_context: spawn UNKNOWN\nprivate details")):
            result = execute_safely(self.browser, "login")
        self.assertEqual(result["status"], "failed")
        self.assertIn("spawn UNKNOWN", result["message"])
        self.assertIn("msedge", result["message"])
        self.assertNotIn("private details", result["message"])

    def test_login_error_does_not_recommend_redundant_headed_flag(self):
        with patch.object(self.browser, "execute", side_effect=PlaywrightError("Page.goto: timeout")):
            result = execute_safely(self.browser, "login")
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("--headed", result["message"])

    def test_zero_and_negative_are_confirmed(self):
        for award in (0, -5):
            self.browser.result["points_change"] = award
            result = self.engine.checkin("lucky")
            self.assertTrue(result["record_saved"])
            self.assertEqual(self.engine.status()["record"]["points_change"], award)
            self.now += timedelta(days=1)

    def test_repeat_cannot_erase_award(self):
        self.browser.result["multiplier"] = 0
        self.engine.checkin("lucky")
        self.browser.result = {"status": "already_signed", "points_change": None,
                               "balance": None, "multiplier": None}
        self.engine.checkin("normal")
        self.assertEqual(self.engine.status()["record"]["points_change"], 0)
        self.assertEqual(self.engine.status()["record"]["multiplier"], 0)

    def test_failure_preserves_prior_success(self):
        self.engine.checkin()
        self.browser.result = {"status": "unknown"}
        self.engine.checkin()
        self.assertEqual(self.engine.status()["status"], "signed")

    def test_unknown_is_not_persisted_as_signed(self):
        self.browser.result = {"status": "unknown"}
        self.engine.checkin()
        self.assertEqual(self.engine.status()["status"], "unknown")

    def test_scheduler_skips_confirmed_but_api_attempts(self):
        self.engine.checkin()
        self.engine.checkin(scheduled=True)
        self.assertEqual(len(self.browser.calls), 1)
        self.engine.checkin()
        self.assertEqual(len(self.browser.calls), 2)

    def test_concurrent_operation_is_busy(self):
        with self.engine.lock:
            self.assertEqual(self.engine.checkin()["status"], "busy")
            self.assertEqual(self.engine.account()["status"], "busy")
        self.assertEqual(self.browser.calls, [])

    def test_records_are_isolated_by_account(self):
        self.engine.checkin()
        other = Engine(self.cfg, "456", self.browser, lambda: self.now)
        self.assertEqual(other.status()["status"], "unknown")

    def test_midnight_does_not_mark_wrong_day(self):
        start = self.now.replace(hour=23, minute=59, second=59)
        with patch.object(self.engine, "clock", side_effect=[start, start + timedelta(seconds=2)]):
            result = self.engine.checkin()
        self.assertIsNone(result["date"])
        self.assertFalse(result["record_saved"])
        self.assertEqual(self.engine.status()["status"], "unknown")

    def test_scheduler_waits_then_runs_once(self):
        self.now = self.now.replace(hour=8)
        scheduler = Scheduler(self.engine)
        scheduler.tick()
        self.assertEqual(self.browser.calls, [])
        self.now += timedelta(hours=1)
        scheduler.tick()
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 1)

    def test_retry_budget_survives_restart(self):
        self.browser.result = {"status": "unknown"}
        scheduler = Scheduler(self.engine)
        scheduler.tick()
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 1)
        for _ in range(4):
            self.now += timedelta(seconds=60)
            Scheduler(self.engine).tick()
        self.assertEqual(len(self.browser.calls), 3)

    def test_login_failure_not_retried(self):
        self.browser.result = {"status": "login_required"}
        scheduler = Scheduler(self.engine)
        scheduler.tick()
        self.now += timedelta(minutes=2)
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 1)

    def test_no_catchup_skips_start_day(self):
        self.engine.cfg = replace(self.cfg, catch_up=False)
        self.now += timedelta(hours=1)
        scheduler = Scheduler(self.engine)
        scheduler.tick()
        self.assertEqual(self.browser.calls, [])
        self.now += timedelta(days=1)
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 1)

    def test_cli_run_ignores_record_and_writes_nothing(self):
        self.engine.checkin()
        before = {p: p.read_bytes() for p in self.cfg.data.rglob("*") if p.is_file()}
        with patch("app.load_settings", return_value=self.cfg), patch("app.Browser", return_value=self.browser), patch("sys.stdout", new_callable=io.StringIO) as console:
            code = main(["run", "--mode", "lucky"])
            result = json.loads(console.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(self.browser.calls), 2)
        after = {p: p.read_bytes() for p in self.cfg.data.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.cfg.data / "logs").exists())

    def test_bad_configuration_is_rejected(self):
        path = Path(self.temp.name) / "bad.toml"
        for content in ('[checkin]\nmode="typo"', '[schedule]\nretries=-1', '[schedule]\nenabled="false"',
                        '[api]\nhost="0.0.0.0"', '[browser]\nunknown=1'):
            path.write_text(content, encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
                load_settings(path)

    def test_history_date_filter(self):
        self.engine.checkin()
        self.now += timedelta(days=1)
        self.engine.checkin()
        history = self.engine.store.history("2026-10-12", "2026-10-12")
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["items"][0]["date"], "2026-10-12")

    def test_corrupt_record_does_not_trigger_automatic_signin(self):
        atomic_json(self.engine.store.path("2026-10-11"), {"status": "bogus"})
        with self.assertRaises(ValueError):
            Scheduler(self.engine).tick()
        self.assertEqual(self.browser.calls, [])

    def test_midnight_status_changes_without_erasing_yesterday(self):
        """模拟零点，不修改系统时间或真实签到记录。"""
        self.now = self.now.replace(hour=23, minute=59, second=59)
        self.engine.checkin()
        calls = len(self.browser.calls)
        self.now += timedelta(seconds=1)
        self.assertEqual(self.engine.status()['status'], 'unknown')
        self.assertEqual(self.engine.status()['date'], '2026-10-12')
        self.assertIsNotNone(self.engine.store.get('2026-10-11'))
        self.assertEqual(len(self.browser.calls), calls)

    def test_yesterday_success_does_not_skip_tomorrow_schedule(self):
        scheduler = Scheduler(self.engine)
        scheduler.tick()
        self.now = self.now.replace(hour=0) + timedelta(days=1)
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 1)
        self.now = self.now.replace(hour=9)
        scheduler.tick()
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 2)
        self.assertEqual(self.engine.status()['status'], 'signed')

    def test_next_day_gets_fresh_retry_budget(self):
        self.browser.result = {'status': 'unknown'}
        scheduler = Scheduler(self.engine)
        for _ in range(3):
            scheduler.tick()
            self.now += timedelta(seconds=60)
        self.assertEqual(len(self.browser.calls), 3)
        self.now += timedelta(days=1)
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 4)

    def test_disabled_schedule_never_opens_browser(self):
        self.engine.cfg = replace(self.cfg, enabled=False)
        scheduler = Scheduler(self.engine)
        for _ in range(3):
            scheduler.tick()
            self.now += timedelta(days=1)
        self.assertEqual(self.browser.calls, [])
        self.assertFalse(self.cfg.data.exists())

    def test_restart_after_success_uses_persisted_confirmation(self):
        self.engine.checkin()
        replacement = Engine(self.cfg, '123', self.browser, lambda: self.now)
        Scheduler(replacement).tick()
        self.assertEqual(len(self.browser.calls), 1)
        self.assertEqual(replacement.status()['status'], 'signed')

    def test_save_failure_preserves_website_result_and_releases_lock(self):
        with patch.object(self.engine.store, 'save', side_effect=OSError('disk full')):
            result = self.engine.checkin()
        self.assertEqual(result['status'], 'success')
        self.assertFalse(result['record_saved'])
        self.assertEqual(self.engine.status()['status'], 'unknown')
        self.assertFalse(self.engine.lock.locked())

    def test_manual_confirmation_prevents_pending_retry(self):
        self.browser.result = {'status': 'unknown'}
        scheduler = Scheduler(self.engine)
        scheduler.tick()
        self.browser.result = {'status': 'already_signed', 'points_change': None, 'balance': None}
        self.engine.checkin()
        self.now += timedelta(seconds=60)
        scheduler.tick()
        self.assertEqual(len(self.browser.calls), 2)
        self.assertEqual(self.engine.status()['status'], 'signed')

    def test_invalid_mode_never_reaches_browser(self):
        with self.assertRaises(ValueError):
            self.engine.checkin('invalid')
        self.assertEqual(self.browser.calls, [])
