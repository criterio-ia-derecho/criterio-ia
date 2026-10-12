import copy
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import send_notice as n
import smoke_draft as s


class SmokeTests(unittest.TestCase):
    def draft(self):
        return {**s.smoke_payload(), 'id': 'em_technical',
                'absolute_url': 'https://buttondown.com/jurilupaia/archive/technical'}

    def test_draft_is_explicitly_technical_and_never_regular_notice(self):
        data = s.smoke_payload()
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['subject'], 'Prueba técnica · JuriLupa IA')
        self.assertEqual(data['archival_mode'], 'disabled')
        self.assertFalse(data['should_trigger_pay_per_email_billing'])
        self.assertNotIn('jurilupaia_article', data['metadata'])
        self.assertNotIn('recipients', data)
        self.assertNotIn('subscribers', data)
        self.assertIn(s.PUBLIC_ARTICLE['slug'], n.BACKLOG)

    def test_creation_only_uses_draft_endpoint(self):
        bd = Mock(); bd.existing.return_value = None; bd.call.return_value = self.draft()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(s.prepare('a'*40, bd, lambda e, sha: None, lambda sha, path: b'public'), 'em_technical')
        bd.call.assert_called_once_with('POST', 'emails', body=s.smoke_payload(), key=s.CREATE_KEY)
        bd.publish.assert_not_called()
        bd.retrieve.assert_not_called()

    def test_reuses_existing_draft_without_mutation(self):
        bd = Mock(); bd.existing.return_value = self.draft()
        with redirect_stdout(io.StringIO()):
            s.prepare('a'*40, bd, lambda e, sha: None, lambda sha, path: b'public')
        bd.call.assert_not_called(); bd.publish.assert_not_called()

    def test_changed_or_wrong_account_draft_stops(self):
        for field, value in [('body', 'changed'), ('status', 'sent'), ('absolute_url', 'https://buttondown.com/other/archive/x')]:
            draft = self.draft(); draft[field] = value
            with self.subTest(field=field), self.assertRaises(n.Stop): s.verify_smoke(draft)

    def test_free_account_gate_before_network(self):
        with patch.dict(s.os.environ, {}, clear=True), patch.object(s, 'GitHub') as gh:
            with self.assertRaises(n.Stop): s.main()
            gh.assert_not_called()


if __name__ == '__main__': unittest.main()
