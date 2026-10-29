import unittest

from autozzz.onedragon_notify import credential_fragments, redact, validate_headers, validate_modes


class NotificationRulesTests(unittest.TestCase):
    def test_credentials_and_addresses_are_removed_from_errors(self):
        text = 'failed https://host.invalid/token/path Bearer fixture-secret'
        result = redact(text, ['fixture-secret'])
        for private in ('host.invalid', '/token/path', 'fixture-secret'): self.assertNotIn(private, result)
        self.assertIn('failed', result)

    def test_nested_header_payload_and_url_tokens_are_also_private(self):
        values = ['Authorization: Bearer header-secret', '{"key":"payload-secret"}', 'https://host.invalid/path-token?key=query-secret']
        result = redact('header-secret payload-secret path-token query-secret', credential_fragments(values))
        self.assertNotIn('secret', result)
        self.assertNotIn('path-token', result)

    def test_headers_accept_both_upstream_formats(self):
        for text in ('', '{"Authorization":"Bearer token"}', 'X-Key: token\nOther: value'):
            validate_headers(text)
        for text in ('{"a":1}', '{broken', 'missing colon'):
            with self.assertRaises(ValueError): validate_headers(text)

    def test_proxy_userinfo_and_gateway_addresses_are_private(self):
        secrets = credential_fragments(['socks5h://proxy-user:p%40ssword@proxy.invalid:1080'])
        result = redact('proxy-user p@ssword socks4a://another.invalid:1080 wss://gateway.invalid/path?secret=value', secrets)
        for private in ('proxy-user', 'p@ssword', 'another.invalid', 'gateway.invalid', 'secret=value'):
            self.assertNotIn(private, result)

    def test_modes_reject_missing_duplicate_and_unknown_entries(self):
        rows = [dict(app_id='a', lifecycle='finish_only', detail='error_only')]
        validate_modes(rows, {'a': '应用'})
        for invalid in ([], rows * 2, [dict(app_id='a', lifecycle='other', detail='off')],
                        [dict(rows[0], extra=True)], [dict(app_id=[], lifecycle='off', detail='off')]):
            with self.assertRaises(ValueError): validate_modes(invalid, {'a': '应用'})
