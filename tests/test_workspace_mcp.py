import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from relay.workspace_mcp import ENTRY_LIMIT, dispatch


class WorkspaceTools(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'reference'
        self.root.mkdir()

    def call(self, arguments):
        return dispatch(self.root, {
            'method': 'tools/call', 'params': {'name': 'inspect_folder', 'arguments': arguments},
        })

    def test_inspects_project_folders_and_counts_script_lines_without_file_contents(self):
        folder = self.root / 'scripts'
        folder.mkdir()
        (folder / 'test.gd').write_bytes(b'extends Node\r\n\r\nprint(42)\r\n')
        (folder / 'test.txt').write_text('skip this', encoding='utf-8')
        (folder / 'subfolder').mkdir()
        result = self.call({'path': 'scripts', 'extension': '.GD', 'count_lines': True})
        text = result['content'][0]['text']
        entries = json.loads(text)['entries']
        self.assertEqual([(entry['name'], entry['lines']) for entry in entries], [('test.gd', 3)])
        self.assertNotIn('print(42)', text)
        self.assertFalse(result.get('isError'))
        inventory = json.loads(self.call({'path': 'scripts'})['content'][0]['text'])['entries']
        self.assertEqual([(entry['type'], entry['name']) for entry in inventory], [
            ('directory', 'subfolder'), ('file', 'test.gd'), ('file', 'test.txt'),
        ])

    def test_keeps_inspection_under_the_reference_folder(self):
        outside = self.base / 'outside'
        outside.mkdir()
        for path in ('../outside', str(outside), 'C:outside', '/outside', 'folder/../outside', 'COM1'):
            with self.subTest(path=path):
                self.assertTrue(self.call({'path': path})['isError'])
        link = self.root / 'alias'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            return
        self.assertTrue(self.call({'path': 'alias'})['isError'])
        self.assertEqual(json.loads(self.call({})['content'][0]['text'])['entries'], [])

    def test_pages_an_inventory(self):
        for number in range(ENTRY_LIMIT + 1):
            (self.root / f'example-{number:03}.gd').touch()
        first = json.loads(self.call({})['content'][0]['text'])
        self.assertEqual(len(first['entries']), ENTRY_LIMIT)
        second = json.loads(self.call({'offset': first['next_offset']})['content'][0]['text'])
        self.assertEqual(len(second['entries']), 1)
        self.assertIsNone(second['next_offset'])
