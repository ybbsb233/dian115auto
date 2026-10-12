import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from checkin import CheckinError, LoginRequired, SITE, perform_checkin, restore_cookies, parse_account, fetch_account, save_login
from storage import atomic_json


class BrowserFlowTests(unittest.TestCase):
    def test_automatic_login_submits_env_credentials_and_confirms_account(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        submitted = []
        html = '''<meta charset="utf-8"><input type="email"><input type="password"><button onclick="login()">登录进入</button>
          <script>async function login(){await fetch('/api/portal/auth/login', {
          method:'POST',body:JSON.stringify({email:document.querySelector('[type=email]').value,
          password:document.querySelector('[type=password]').value})});}</script>'''
        def respond(route):
            if route.request.url.endswith('/api/portal/auth/login'):
                submitted.append(json.loads(route.request.post_data))
                route.fulfill(json={'user': {'id': 1, 'points': 179, 'nickname': 'fixture'}})
            else:
                route.fulfill(content_type='text/html', body=html)
        context.route('**/*', respond)
        with patch.dict(os.environ, {'DIAN115_EMAIL':'fixture@example.com','DIAN115_PASSWORD':'fixture-password'}):
            result = save_login(context.new_page(), 5)
        self.assertEqual(result['account_id'], '1')
        self.assertEqual(submitted, [{'email':'fixture@example.com','password':'fixture-password'}])

    def test_partial_login_credentials_rejected_before_navigation(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        with patch.dict(os.environ, {'DIAN115_EMAIL':'fixture@example.com','DIAN115_PASSWORD':''}), \
             self.assertRaises(ValueError):
            save_login(page, 2)
        self.assertEqual(page.url, 'about:blank')

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        channel = os.environ.get("DIAN115_TEST_BROWSER", "chromium")
        cls.browser_options = {} if channel == "chromium" else {"channel": channel}
        cls.browser = cls.playwright.chromium.launch(**cls.browser_options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def run_fixture(self, status, data, login=False, mode="normal", structured=False, proof_retry=False):
        context = self.browser.new_context()
        requests = []
        html = '''<meta charset="utf-8"><script>
          async function sign(mode) {
            let response = await fetch('/api/portal/signin', {method:'POST',body:JSON.stringify({mode})});
            if (response.status === 403) await fetch('/api/portal/signin', {method:'POST',body:JSON.stringify({mode})});
          }
          </script><button onclick="sign('normal')">普通签到</button>
          <button onclick="sign('lucky')">运气签到</button>'''

        def route_handler(route):
            if route.request.url.endswith('/api/portal/signin'):
                requests.append(json.loads(route.request.post_data))
                if proof_retry and len(requests) == 1:
                    route.fulfill(status=403, json={'code': 'browser_proof_required'})
                    return
                route.fulfill(status=status, json=data)
            elif login:
                route.fulfill(content_type='text/html', body="<script>history.replaceState({}, '', '/login')</script>")
            else:
                route.fulfill(content_type='text/html; charset=utf-8', body=html)

        context.route('**/*', route_handler)
        try:
            result = perform_checkin(context.new_page(), 3, mode, structured)
            self.assertEqual(requests, [{'mode': mode}] * (2 if proof_retry else 1))
            return result
        finally:
            context.close()

    def test_normal_signin(self):
        self.assertIn('签到成功', self.run_fixture(200, {'award': 5, 'new_balance': 30}))

    def test_repeat_signin(self):
        self.assertIn('已签到', self.run_fixture(409, {'code': 'already_signed'}))

    def test_stale_session_rejected_by_server(self):
        with self.assertRaises(LoginRequired):
            self.run_fixture(401, {'code': 'invalid_token'})

    def test_login_redirect(self):
        with self.assertRaises(LoginRequired):
            self.run_fixture(200, {}, login=True)

    def test_click_without_business_result_is_not_success(self):
        with self.assertRaises(CheckinError):
            self.run_fixture(200, {})

    def test_lucky_zero_and_loss(self):
        for award in (0, -5):
            result = self.run_fixture(200, {'award': award, 'new_balance': 15}, mode='lucky', structured=True)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['mode'], 'lucky')
            self.assertEqual(result['points_change'], award)

    def test_website_browser_proof_retry_is_allowed_to_finish(self):
        result = self.run_fixture(200, {'award': 5, 'new_balance': 20}, proof_retry=True)
        self.assertIn('签到成功', result)

    def test_session_cookies_restore_after_browser_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            context = self.playwright.chromium.launch_persistent_context(str(path / 'profile'), headless=True, **self.browser_options)
            context.add_cookies([{'name': 'test_session', 'value': 'fixture', 'url': SITE}])
            atomic_json(path / 'cookies.json', context.cookies())
            context.close()
            context = self.playwright.chromium.launch_persistent_context(str(path / 'profile'), headless=True, **self.browser_options)
            try:
                restore_cookies(context, path / 'cookies.json')
                self.assertTrue(any(c['name'] == 'test_session' for c in context.cookies()))
            finally:
                context.close()

    def test_account_shape_and_expired_login(self):
        result = parse_account(200, {'user': {'id': 1, 'points': 0, 'nickname': 'test'}})
        self.assertEqual(result['balance'], 0)
        with self.assertRaises(LoginRequired):
            parse_account(401, {})

    def test_account_guest_redirect_without_api_response(self):
        context = self.browser.new_context()
        try:
            context.route('**/*', lambda route: route.fulfill(content_type='text/html',
                body="<script>history.replaceState({}, '', '/login')</script>"))
            with self.assertRaises(LoginRequired):
                fetch_account(context.new_page(), 3)
        finally:
            context.close()

    def test_account_browser_verification_rejected(self):
        context = self.browser.new_context()
        def respond(route):
            if route.request.url.endswith('/auth/browser-challenge'):
                route.fulfill(status=403, json={'code': 'forbidden'})
            else:
                route.fulfill(content_type='text/html', body="<script>fetch('/api/portal/auth/browser-challenge')</script>")
        try:
            context.route('**/*', respond)
            with self.assertRaisesRegex(CheckinError, 'HTTP 403'):
                fetch_account(context.new_page(), 1)
        finally:
            context.close()
