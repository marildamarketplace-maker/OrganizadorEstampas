import unittest
from urllib.error import HTTPError

from meury_app.retry_policy import RetryFailure, run_with_retry


class RetryPolicyTest(unittest.TestCase):
    def test_transient_error_retries_then_succeeds(self):
        calls = []

        def action():
            calls.append(True)
            if len(calls) < 3:
                raise HTTPError("https://example", 503, "indisponível", {}, None)
            return "ok"

        value, attempts = run_with_retry(action, sleep=lambda _seconds: None)
        self.assertEqual((value, attempts, len(calls)), ("ok", 3, 3))

    def test_permanent_error_is_not_retried(self):
        calls = []

        def action():
            calls.append(True)
            raise ValueError("payload inválido")

        with self.assertRaises(RetryFailure) as captured:
            run_with_retry(action, sleep=lambda _seconds: None)
        self.assertEqual(captured.exception.attempts, 1)
        self.assertEqual(len(calls), 1)

    def test_google_style_status_code_is_classified(self):
        transient = RuntimeError("temporário")
        transient.code = 503
        permanent = RuntimeError("negado")
        permanent.code = 403
        from meury_app.retry_policy import is_retryable_external_error
        self.assertTrue(is_retryable_external_error(transient))
        self.assertFalse(is_retryable_external_error(permanent))


if __name__ == "__main__":
    unittest.main()
