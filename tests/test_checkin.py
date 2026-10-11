import unittest
from checkin import CheckinError, LoginRequired, interpret_response


class ResultTests(unittest.TestCase):
    def test_success_requires_award_and_balance(self):
        self.assertIn("+5", interpret_response(200, {"award": 5, "new_balance": 20}))

    def test_already_signed_is_success(self):
        self.assertIn("已签到", interpret_response(409, {"code": "already_signed"}))

    def test_expired_session(self):
        for status, data in [(401, {}), (200, {"code": "token_revoked"})]:
            with self.subTest(status=status), self.assertRaises(LoginRequired):
                interpret_response(status, data)

    def test_rejects_false_success(self):
        cases = [(200, {}), (200, []), (200, {"code": "forbidden", "award": 5, "new_balance": 20}),
                 (200, {"award": True, "new_balance": 20}),
                 (500, {"award": 5, "new_balance": 20}), (429, {"code": "already_signed"})]
        for status, data in cases:
            with self.subTest(status=status, data=data), self.assertRaises(CheckinError):
                interpret_response(status, data)


if __name__ == "__main__":
    unittest.main()
