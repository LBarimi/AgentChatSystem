from http.client import HTTPConnection
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest
from urllib.parse import urlencode

import test_network


class FilePage(unittest.TestCase):
    setUp = test_network.NetworkAccess.setUp
    stop = test_network.NetworkAccess.stop
    call = test_network.NetworkAccess.call
    download = test_network.NetworkAccess.download

    def get(self, route, host=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request('GET', route, headers={'Host': host or f'192.168.1.20:{self.port}'})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_file_page_and_assets_are_served_over_lan_with_existing_host_checks(self):
        for route, mime in [('/files?channel=general&kind=shared&path=docs', 'text/html'),
                            ('/files.js', 'text/javascript'), ('/files.css', 'text/css'), ('/theme.js', 'text/javascript')]:
            with self.subTest(route=route):
                status, headers, content = self.get(route)
                self.assertEqual(status, 200)
                self.assertIn(mime, headers['Content-Type'])
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertTrue(content)
                status, _, _ = self.get(route, f'example.com:{self.port}')
                self.assertEqual(status, 403)

    def test_nested_unicode_folder_links_and_downloads_keep_channel_scope(self):
        room = self.server.room
        channel = room.create_channel({'name': 'Documents'})
        nested = Path(channel['shared']) / 'docs' / '검토 #1 & final'
        nested.mkdir(parents=True)
        data = b'Example download\n'
        (nested / 'notes #1.txt').write_bytes(data)
        params = {'channel': channel['id'], 'kind': 'shared', 'path': 'docs/검토 #1 & final'}
        status, result = self.call('/api/files?' + urlencode(params), host=f'192.168.1.20:{self.port}')
        self.assertEqual(status, 200)
        self.assertEqual(result['entries'][0]['path'], params['path'] + '/notes #1.txt')
        status, _, actual = self.download(**dict(params, path=result['entries'][0]['path']))
        self.assertEqual(status, 200)
        self.assertEqual(actual, data)
        status, _, _ = self.download(**dict(params, channel='general', path=result['entries'][0]['path']))
        self.assertEqual(status, 404)

    def test_both_pages_have_unique_ids_and_complete_translation_keys(self):
        class Page(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.keys = set()
            def handle_starttag(self, tag, attrs):
                for name, value in attrs:
                    if name == 'id':
                        self.ids.append(value)
                    if name.startswith('data-i18n'):
                        self.keys.add(value)
        web = test_network.ROOT / 'src/relay/web'
        for filename in ('index.html', 'files.html'):
            page = Page()
            page.feed((web / filename).read_text(encoding='utf-8'))
            self.assertEqual(len(page.ids), len(set(page.ids)))
            for path in (web / 'locales').glob('*.json'):
                catalog = json.loads(path.read_text(encoding='utf-8'))
                self.assertFalse(page.keys - catalog.keys(), (filename, path.name, page.keys - catalog.keys()))


if __name__ == '__main__':
    unittest.main()
