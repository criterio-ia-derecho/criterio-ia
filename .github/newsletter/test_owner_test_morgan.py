import copy
import contextlib
import hashlib
import io
import unittest
from unittest.mock import Mock, patch

import owner_test_morgan as m
from send_notice import Stop


class OwnerTest(unittest.TestCase):
    def setUp(self):
        self.gh = Mock()
        self.gh.exists.return_value = False
        self.bd = Mock()
        self.bd.existing.return_value = None
        self.draft = dict(m.draft_payload(), id="em_owner_preview")
        self.owner = "owner@example.invalid"
        self.account = {"email_address": self.owner}
        self.bd.call.side_effect = [self.account, self.draft, {}]
        self.bd.retrieve.return_value = copy.deepcopy(self.draft)
        self.verify = Mock()

    def run_test(self):
        with patch.object(m, "OWNER_SHA256", hashlib.sha256(self.owner.encode()).hexdigest()), contextlib.redirect_stdout(io.StringIO()):
            return m.run(self.gh, self.bd, self.verify)

    def test_single_fixed_recipient_and_never_publish(self):
        self.run_test()
        self.verify.assert_called_once_with(m.ARTICLE, m.PUBLISHED_SHA)
        self.gh.reserve.assert_called_once_with(m.RESERVATION_STAGE, m.TEST_SLUG, m.PUBLISHED_SHA)
        calls = [c for c in self.bd.call.call_args_list if c.args[0] == "POST"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args, ("POST", "emails"))
        self.assertEqual(calls[0].kwargs["body"]["status"], "draft")
        self.assertEqual(calls[1].args, ("POST", "emails/em_owner_preview/send-draft"))
        self.assertEqual(calls[1].kwargs["body"], {"recipients": [self.owner], "subscribers": []})
        self.assertNotEqual(calls[0].kwargs["key"], calls[1].kwargs["key"])

    def test_existing_marker_stops_before_provider(self):
        self.gh.exists.return_value = True
        with self.assertRaises(Stop): self.run_test()
        self.assertEqual(self.bd.mock_calls, [])
        self.gh.reserve.assert_not_called()

    def test_existing_draft_never_reused(self):
        self.bd.existing.return_value = self.draft
        with self.assertRaises(Stop): self.run_test()
        self.bd.call.assert_not_called()
        self.gh.reserve.assert_not_called()

    def test_reservation_failure_never_calls_provider(self):
        self.gh.reserve.side_effect = Stop("reservation uncertain")
        with self.assertRaises(Stop): self.run_test()
        self.assertTrue(all(c.args[0] == "GET" for c in self.bd.call.call_args_list))

    def test_reservation_precedes_provider_write(self):
        def create(*args, **kwargs):
            if args[0] == "GET": return self.account
            self.gh.reserve.assert_called_once()
            return self.draft if args[1] == "emails" else {}
        self.bd.call.side_effect = create
        self.run_test()

    def test_create_error_no_retry(self):
        self.bd.call.side_effect = [self.account, Stop("unknown create outcome")]
        with self.assertRaises(Stop): self.run_test()
        self.assertEqual(self.bd.call.call_count, 2)
        self.gh.reserve.assert_called_once()

    def test_send_error_no_retry(self):
        self.bd.call.side_effect = [self.account, self.draft, Stop("unknown send outcome")]
        with self.assertRaises(Stop): self.run_test()
        self.assertEqual(self.bd.call.call_count, 3)
        self.gh.reserve.assert_called_once()

    def test_changed_draft_blocks_send(self):
        for field, value in [("status", "sent"), ("subject", "unexpected"), ("body", "unexpected"), ("should_trigger_pay_per_email_billing", True), ("filters", {"unexpected": True}), ("id", "../../publish"), ("absolute_url", "https://buttondown.com/other/archive/x")]:
            with self.subTest(field=field):
                self.setUp()
                self.bd.retrieve.return_value[field] = value
                with self.assertRaises(Stop): self.run_test()
                self.assertEqual(self.bd.call.call_count, 2)

    def test_live_publication_verification_blocks_before_write(self):
        self.verify.side_effect = Stop("public content mismatch")
        with self.assertRaises(Stop): self.run_test()
        self.bd.call.assert_not_called()
        self.gh.reserve.assert_not_called()

    def test_unexpected_response_not_claimed_delivered(self):
        self.bd.call.side_effect = [self.account, self.draft, {"unexpected": True}]
        with self.assertRaises(Stop): self.run_test()
        self.assertEqual(self.bd.call.call_count, 3)

    def test_wrong_account_owner_blocks_all_writes(self):
        self.bd.call.side_effect = [{"email_address": "someone-else@example.invalid"}]
        with self.assertRaises(Stop): self.run_test()
        self.gh.reserve.assert_not_called()
        self.assertEqual(self.bd.call.call_args.args, ("GET", "accounts/me"))


if __name__ == "__main__":
    unittest.main()
