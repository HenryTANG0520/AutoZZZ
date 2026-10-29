import unittest

from autozzz.onedragon_environment import validate_url


class EnvironmentAddressTests(unittest.TestCase):
    def test_proxy_credentials_schemes_and_ipv6_are_accepted_without_network(self):
        for address in ('http://127.0.0.1:7890', 'https://user:password@proxy.invalid:8443',
                        'socks5h://[::1]:1080', 'socks4a://proxy.invalid', 'http://proxy.invalid/'):
            validate_url(address, proxy=True)
        validate_url('https://proxy.invalid/github')

    def test_invalid_addresses_reject_without_echoing_private_input(self):
        invalid = ('private-password@proxy.invalid:80', 'http://proxy.invalid:65536', 'file://proxy.invalid',
                   'http://proxy.invalid:0', 'http://proxy.invalid:', 'http://proxy.invalid/route',
                   'http://proxy.invalid?q=private-password', 'http://proxy.invalid#private-password',
                   'http://proxy.invalid\n', 'http://proxy.invalid/\0', 'http://proxy.invalid\\route')
        for address in invalid:
            with self.subTest(address=address), self.assertRaises(ValueError) as caught:
                validate_url(address, proxy=True)
            self.assertNotIn('private-password', str(caught.exception))
        with self.assertRaises(ValueError): validate_url('https://user:password@proxy.invalid')
