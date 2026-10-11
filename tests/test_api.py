from dataclasses import replace
from datetime import datetime, time
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from api import make_server
from runtime import Engine
from settings import Settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        root = Path(self.temp.name)
        cfg = Settings(root, root / "profile", ZoneInfo("Asia/Shanghai"), time(9), port=0, api_key="test-key")
        owner = self

        class Browser:
            def execute(self, operation, **kwargs):
                owner.calls.append((operation, kwargs))
                if operation == "account":
                    return {"status": "ok", "account_id": "1", "balance": 15}
                return {"status": "success", "points_change": -5, "balance": 15, "mode": kwargs["mode"]}

        self.engine = Engine(cfg, "1", Browser(), lambda: datetime(2026, 10, 11, 10, tzinfo=cfg.zone))
        self.server = make_server(self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer test-key"
        req = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_status_unknown_is_200_and_does_not_call_website(self):
        status, body = self.request("/checkin/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unknown")
        self.assertEqual(self.calls, [])

    def test_signin_then_local_status_and_history(self):
        status, body = self.request("/checkin", {"mode": "lucky"})
        self.assertEqual(status, 200)
        self.assertEqual(body["points_change"], -5)
        self.assertEqual(self.request("/checkin/status")[1]["status"], "signed")
        self.assertEqual(self.request("/checkin/history")[1]["total"], 1)
        self.assertEqual(len(self.calls), 1)

    def test_authentication_required(self):
        self.assertEqual(self.request("/account", auth=False)[0], 401)
        self.assertEqual(self.calls, [])

    def test_account_queries_browser(self):
        self.assertEqual(self.request("/account")[1]["balance"], 15)
        self.assertEqual(self.calls[0][0], "account")

    def test_bad_requests_do_not_trigger_signin(self):
        for body in ({"mode": "invalid"}, {"mode": None}, {"url": "https://example.com"}, []):
            self.assertEqual(self.request("/checkin", body)[0], 400)
        self.assertEqual(self.request("/checkin/history?size=1000")[0], 400)
        self.assertEqual(self.calls, [])

    def test_get_cannot_signin(self):
        self.assertEqual(self.request("/checkin")[0], 404)
        self.assertEqual(self.calls, [])
