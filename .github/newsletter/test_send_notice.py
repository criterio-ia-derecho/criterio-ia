import copy
import hashlib
import io
import json
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

import send_notice as n

SHA = 'a' * 40
DOC = '<html><h1>Una nota nueva</h1><p>Resumen público breve.</p></html>'.encode()


def entry():
    return {'slug': 'una-nota-nueva', 'title': 'Una nota nueva', 'summary': 'Resumen público breve.',
            'published_date': '2026-10-12', 'article_sha256': hashlib.sha256(DOC).hexdigest(),
            'create_key': str(uuid.uuid4()), 'send_key': str(uuid.uuid4())}


class FakeGH:
    def __init__(self):
        self.tags = set()
        self.calls = []

    def exists(self, stage, slug):
        return (stage, slug) in self.tags

    def reserve(self, stage, slug, sha):
        if (stage, slug) in self.tags:
            raise n.Stop('Existing reservation')
        self.tags.add((stage, slug))
        self.calls.append(stage)


class FakeBD:
    def __init__(self):
        self.email = None
        self.active = 2
        self.creates = self.sends = 0
        self.fail_create = self.fail_send = False
        self.provider_accepts_before_timeout = False
        self.cap_error = False

    def existing(self, e):
        return self.email

    def free_cap(self):
        if self.cap_error:
            raise n.Stop('Cap exceeded')
        return self.active

    def create(self, e):
        self.creates += 1
        if self.fail_create:
            raise n.Stop('Timeout')
        self.email = {**n.payload(e), 'id': 'em_123', 'absolute_url': 'https://buttondown.com/jurilupaia/archive/nota-' + e['slug'],
                      'filters': {'filters': [], 'groups': [], 'predicate': 'and'}}
        return self.email

    def retrieve(self, id):
        return self.email

    def publish(self, e, id):
        self.sends += 1
        if self.fail_send:
            if self.provider_accepts_before_timeout:
                self.email['status'] = 'about_to_send'
            raise n.Stop('Timeout')
        self.email['status'] = 'about_to_send'
        return self.email


class QueueTests(unittest.TestCase):
    def test_empty_queue_excludes_all_backlog(self):
        self.assertEqual(n.validate_queue({'version': 1, 'entries': []}), [])
        self.assertEqual(len(n.BACKLOG), 3)

    def test_removed_or_changed_queue_entry_cancels_stale_work(self):
        e = entry()
        self.assertEqual(n.current_entries([e], [e]), [e])
        self.assertEqual(n.current_entries([e], []), [])
        changed = {**e, 'summary': 'Corrected summary.'}
        self.assertEqual(n.current_entries([e], [changed]), [])

    def test_valid_new_article(self):
        e = entry()
        self.assertEqual(n.validate_queue({'version': 1, 'entries': [e]}), [e])

    def test_each_backlog_article_is_rejected(self):
        for slug in n.BACKLOG:
            e = entry(); e['slug'] = slug
            with self.subTest(slug=slug), self.assertRaises(n.Stop):
                n.validate_queue({'version': 1, 'entries': [e]})

    def test_duplicate_slug_and_keys_rejected(self):
        e = entry()
        with self.assertRaises(n.Stop): n.validate_queue({'version': 1, 'entries': [e, e]})
        other = copy.deepcopy(e); other['slug'] = 'another'
        with self.assertRaises(n.Stop): n.validate_queue({'version': 1, 'entries': [e, other]})

    def test_extra_field_and_path_injection_rejected(self):
        for field, value in [('recipient', 'x@example.com'), ('slug', '../other'), ('title', 'x\nBCC: z'),
                             ('summary', '{{ subscribers }}'), ('published_date', '2099-01-01')]:
            e = entry(); e[field] = value
            with self.subTest(field=field), self.assertRaises(n.Stop):
                n.validate_queue({'version': 1, 'entries': [e]})

    def test_safe_html_template_and_no_paid_features(self):
        e = entry(); e['summary'] = '<script>alert("x")</script>'
        p = n.payload(e)
        self.assertNotIn('<script>', p['body'])
        self.assertIn('&lt;script&gt;', p['body'])
        self.assertFalse(p['should_trigger_pay_per_email_billing'])
        self.assertEqual(p['archival_mode'], 'disabled')
        self.assertEqual(p['template'], 'classic')
        self.assertNotIn('recipients', p)
        self.assertNotIn('subscriber', p)


class PublicationTests(unittest.TestCase):
    def test_exact_success(self):
        n.verify_publication(entry(), SHA, lambda sha, path: DOC, lambda url: DOC)

    def test_stale_page_rejected(self):
        with self.assertRaises(n.Stop):
            n.verify_publication(entry(), SHA, lambda sha, path: DOC, lambda url: b'old')

    def test_changed_hash_rejected_before_fetch(self):
        with self.assertRaises(n.Stop):
            n.verify_publication(entry(), SHA, lambda sha, path: b'changed', lambda url: self.fail('fetched'))

    def test_nonpublic_summary_rejected(self):
        e = entry(); e['summary'] = 'Private draft text'
        with self.assertRaises(n.Stop):
            n.verify_publication(e, SHA, lambda sha, path: DOC, lambda url: DOC)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.e, self.gh, self.bd = entry(), FakeGH(), FakeBD()
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output); self.redirect.__enter__()

    def tearDown(self): self.redirect.__exit__(None, None, None)

    def run_flow(self, mode='live', verify=lambda e, sha: None):
        return n.process(self.e, SHA, mode, self.gh, self.bd, verify)

    def test_preview_no_mutations_or_provider_calls(self):
        self.bd.free_cap = lambda: self.fail('Provider called')
        self.bd.existing = lambda e: self.fail('Provider called')
        self.assertEqual(self.run_flow('preview'), 'preview')
        self.assertEqual(self.gh.calls, [])

    def test_draft_never_sends(self):
        self.assertEqual(self.run_flow('draft'), 'draft')
        self.assertEqual((self.bd.creates, self.bd.sends), (1, 0))
        self.assertEqual(self.gh.calls, ['create'])

    def test_draft_can_be_reviewed_with_zero_subscribers(self):
        self.bd.active = 0
        self.assertEqual(self.run_flow('draft'), 'draft')
        self.assertEqual((self.bd.creates, self.bd.sends), (1, 0))
        self.assertEqual(self.run_flow('live'), 'skipped')
        self.bd.active = 2
        self.assertEqual(self.run_flow('live'), 'skipped')
        self.assertEqual(self.bd.sends, 0)

    def test_draft_to_live_sends_same_draft_once(self):
        self.run_flow('draft')
        self.run_flow('live')
        self.run_flow('live')
        self.assertEqual((self.bd.creates, self.bd.sends), (1, 1))

    def test_live_rerun_and_css_deploy_never_duplicate(self):
        self.run_flow()
        self.run_flow(verify=lambda e, sha: self.fail('Old sent article revalidated'))
        self.bd.email['status'] = 'sent'
        self.run_flow()
        self.assertEqual((self.bd.creates, self.bd.sends), (1, 1))

    def test_failed_publication_has_no_mutations(self):
        def fail(e, sha): raise n.Stop('Not published')
        with self.assertRaises(n.Stop): self.run_flow(verify=fail)
        self.assertFalse(self.gh.tags)
        self.assertEqual(self.bd.creates, 0)

    def test_create_timeout_rerun_stops_without_second_create(self):
        self.bd.fail_create = True
        with self.assertRaises(n.Stop): self.run_flow()
        self.bd.fail_create = False
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual((self.bd.creates, self.bd.sends), (1, 0))

    def test_send_timeout_rerun_stops_without_second_send(self):
        self.bd.fail_send = True
        with self.assertRaises(n.Stop): self.run_flow()
        self.bd.fail_send = False
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.bd.sends, 1)

    def test_send_timeout_after_provider_accepts_reconciles_read_only(self):
        self.bd.fail_send = self.bd.provider_accepts_before_timeout = True
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.run_flow(), 'about_to_send')
        self.assertEqual(self.bd.sends, 1)

    def test_cap_fails_closed(self):
        self.bd.cap_error = True
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertFalse(self.gh.tags)
        self.assertEqual(self.bd.sends, 0)

    def test_zero_subscribers_not_replayed_to_later_subscribers(self):
        self.bd.active = 0
        self.assertEqual(self.run_flow(), 'skipped')
        self.bd.active = 3
        self.assertEqual(self.run_flow(), 'skipped')
        self.assertEqual((self.bd.creates, self.bd.sends), (0, 0))

    def test_modified_draft_not_overwritten_or_sent(self):
        self.run_flow('draft')
        self.bd.email['body'] = 'Different text'
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.bd.sends, 0)

    def test_concurrent_draft_change_is_revalidated(self):
        self.run_flow('draft')
        calls = [0]
        def cap():
            calls[0] += 1
            if calls[0] == 2:
                self.bd.email['body'] = 'Concurrent alteration'
            return 2
        self.bd.free_cap = cap
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.bd.sends, 0)
        self.assertFalse(self.gh.exists('send', self.e['slug']))

    def test_atomic_send_reservation_rejects_competitor(self):
        self.run_flow('draft')
        original = self.gh.reserve
        def reserve(stage, slug, sha):
            if stage == 'send': self.gh.tags.add((stage, slug))
            return original(stage, slug, sha)
        self.gh.reserve = reserve
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.bd.sends, 0)

    def test_wrong_buttondown_account_not_sent(self):
        self.run_flow('draft')
        self.bd.email['absolute_url'] = 'https://buttondown.com/somebodyelse/archive/test'
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.bd.sends, 0)

    def test_unowned_draft_or_failed_email_never_taken_over(self):
        self.bd.create(self.e)
        with self.assertRaises(n.Stop): self.run_flow()
        self.bd.email['status'] = 'errored'
        with self.assertRaises(n.Stop): self.run_flow()
        self.assertEqual(self.bd.sends, 0)


class ApiTests(unittest.TestCase):
    def test_subscriber_count_conservative_and_missing_count_fails(self):
        bd = n.Buttondown('unit-test-placeholder')
        for counts in [(101, 1), (None, 1), (3, 5), (True, 1)]:
            with self.subTest(counts=counts), patch.object(bd, 'call', side_effect=[{'count': counts[0]}, {'count': counts[1]}]):
                with self.assertRaises(n.Stop): bd.free_cap()
        with patch.object(bd, 'call', side_effect=[{'count': 100}, {'count': 2}]):
            self.assertEqual(bd.free_cap(), 2)

    def test_unverifiable_or_wrong_account_stops_before_write(self):
        bd = n.Buttondown('unit-test-placeholder')
        for response in ({'count': 0, 'results': []}, {'count': 1, 'results': [{'absolute_url': 'https://buttondown.com/other/archive/test'}]}):
            with patch.object(bd, 'call', return_value=response) as call:
                with self.assertRaises(n.Stop): bd.existing(entry())
                self.assertTrue(all(args[0] == 'GET' for args, kw in call.call_args_list))

    def test_provider_write_requires_read_only_account_verification(self):
        bd = n.Buttondown('unit-test-placeholder')
        with patch.object(n, 'request') as req:
            with self.assertRaises(n.Stop): bd.call('POST', 'emails', body={})
            req.assert_not_called()

    def test_canonical_alias_never_proves_account_identity(self):
        bd, e = n.Buttondown('unit-test-placeholder'), entry()
        forged = {'id': 'em_1', 'canonical_url': 'https://buttondown.com/jurilupaia/archive/test',
                  'absolute_url': 'https://buttondown.com/jurilupaia/archive/test'}
        with patch.object(bd, 'call', return_value={'count': 1, 'results': [forged]}):
            with self.assertRaises(n.Stop): bd.existing(e)
        self.assertFalse(bd.account_verified)

    def test_hidden_and_canonical_records_allowed_with_real_namespace_seed(self):
        bd, e = n.Buttondown('unit-test-placeholder'), entry()
        seed = {'id': 'em_seed', 'absolute_url': 'https://buttondown.com/jurilupaia/archive/seed', 'canonical_url': ''}
        canonical = {**n.payload(e), 'id': 'em_2', 'absolute_url': n.article_url(e)}
        hidden = {'id': 'em_3', 'absolute_url': None, 'canonical_url': ''}
        editor = {'id': 'em_4', 'absolute_url': 'https://buttondown.com/emails/em_4', 'canonical_url': ''}
        with patch.object(bd, 'call', return_value={'count': 4, 'results': [seed, canonical, hidden, editor]}):
            self.assertEqual(bd.existing(e), canonical)
        self.assertTrue(bd.account_verified)

    def test_no_credentials_sent_to_other_hosts(self):
        with self.assertRaises(n.Stop):
            n.request('GET', 'https://example.com', token='unit-test-placeholder')

    def test_complete_provider_history_and_duplicates(self):
        bd, e = n.Buttondown('unit-test-placeholder'), entry()
        match = {**n.payload(e), 'id': 'em_1', 'absolute_url': 'https://buttondown.com/jurilupaia/archive/test'}
        with patch.object(bd, 'call', side_effect=[{'count': 2, 'results': [{'id': 'unrelated'}]}, {'count': 2, 'results': [match]}]):
            self.assertEqual(bd.existing(e), match)
        with patch.object(bd, 'call', return_value={'count': 2, 'results': [match, match]}):
            with self.assertRaises(n.Stop): bd.existing(e)
        with patch.object(bd, 'call', return_value={'count': 2, 'results': []}):
            with self.assertRaises(n.Stop): bd.existing(e)

    def test_stable_keys_are_passed_to_both_mutations(self):
        bd, e = n.Buttondown('unit-test-placeholder'), entry()
        with patch.object(bd, 'call', return_value={}) as call:
            bd.create(e)
            self.assertEqual(call.call_args.kwargs['key'], e['create_key'])
            self.assertEqual(call.call_args.kwargs['body']['status'], 'draft')
            bd.publish(e, 'em_123')
            self.assertEqual(call.call_args.kwargs['key'], e['send_key'])
            self.assertEqual(call.call_args.args, ('POST', 'emails/em_123/publish'))
            self.assertEqual(call.call_args.kwargs['body']['body'], n.payload(e)['body'])
            self.assertEqual(call.call_args.kwargs['body']['filters'], {'filters': [], 'groups': [], 'predicate': 'and'})
            self.assertNotIn('status', call.call_args.kwargs['body'])

    def test_provider_writes_remain_disabled_despite_live_flags(self):
        env = {'NEWSLETTER_MODE': 'live', 'NEWSLETTER_LIVE_APPROVED': 'true',
               'NEWSLETTER_FREE_ACCOUNT': 'free-no-payment-method'}
        with patch.dict(n.os.environ, env, clear=True), patch.object(n, 'request') as req:
            with self.assertRaises(n.Stop): n.main()
            req.assert_not_called()

    def test_live_disabled_before_any_network_call(self):
        with patch.dict(n.os.environ, {'NEWSLETTER_MODE': 'live'}, clear=True), patch.object(n, 'request') as req:
            with self.assertRaises(n.Stop): n.main()
            req.assert_not_called()

    def test_wrong_or_unsuccessful_pages_run_rejected(self):
        gh = n.GitHub(None)
        for run in ({}, {'status': 'completed', 'conclusion': 'failure'}, {'status': 'completed', 'conclusion': 'success', 'head_branch': 'other'}):
            with patch.object(gh, 'call', return_value=run):
                with self.assertRaises(n.Stop): gh.verify_run('123')


if __name__ == '__main__': unittest.main()
