import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deck', Path(__file__).resolve().parents[1] / 'lib/deck.py')
deck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deck)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='deck-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.state = self.root / 'state'
        self.versions = self.data / 'versions'
        self.versions.mkdir(parents=True)
        self.state.mkdir()
        for name, value in [('DATA', self.data), ('STATE', self.state), ('VERSIONS', self.versions),
                            ('CURRENT', self.data / 'current')]:
            patcher = patch.object(deck, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for version in ('26.1.0', '26.2.0'):
            path = self.versions / version
            path.mkdir()
            (path / 'ChatGPT').write_text(version)
        self.artifacts = self.data / 'artifacts/26.2.0'
        self.artifacts.mkdir(parents=True)
        (self.artifacts / 'package.deb').write_text('fake package')
        self.pending = self.state / 'pending.json'
        self.tx = {'old': '26.1.0', 'new': '26.2.0', 'sha256': deck.sha(self.versions / '26.2.0/ChatGPT')}
        deck.atomic_json(self.pending, self.tx)
        deck.activate('26.2.0')

    def test_confirm_only_removes_managed_old_and_artifacts(self):
        other = self.data / 'personal-data'
        other.write_text('keep')
        deck.confirm('26.2.0')
        self.assertFalse((self.versions / '26.1.0').exists())
        self.assertFalse(self.artifacts.exists())
        self.assertTrue((self.versions / '26.2.0/ChatGPT').exists())
        self.assertEqual(other.read_text(), 'keep')
        self.assertFalse(self.pending.exists())
        deck.confirm('26.2.0')  # Idempotent after successful cleanup.

    def test_changed_binary_keeps_rollback(self):
        (self.versions / '26.2.0/ChatGPT').write_text('changed')
        with self.assertRaises(RuntimeError):
            deck.confirm('26.2.0')
        self.assertTrue((self.versions / '26.1.0').exists())
        self.assertTrue(self.artifacts.exists())

    def test_symlink_cleanup_target_is_refused(self):
        old = self.versions / '26.1.0'
        old.rename(self.root / 'outside')
        old.symlink_to(self.root / 'outside')
        with self.assertRaises(RuntimeError):
            deck.confirm('26.2.0')
        self.assertTrue((self.root / 'outside/ChatGPT').exists())

    def test_unconfirmed_update_can_roll_back(self):
        deck.rollback()
        self.assertEqual(deck.current(), '26.1.0')
        self.assertTrue(self.artifacts.exists())
        self.assertTrue((self.versions / '26.2.0').exists())

    def test_first_install_has_no_rollback(self):
        self.tx['old'] = None
        deck.atomic_json(self.pending, self.tx)
        with self.assertRaises(RuntimeError):
            deck.rollback()
        self.assertEqual(deck.current(), '26.2.0')

    def test_wrong_launch_never_cleans(self):
        deck.confirm('26.1.0')
        self.assertTrue(self.artifacts.exists())

    def test_current_escape_refused(self):
        deck.CURRENT.unlink()
        deck.CURRENT.symlink_to(self.root)
        with self.assertRaises(RuntimeError):
            deck.current()

    def test_update_lock_conflict_preserves_pending(self):
        with deck.lock('update.lock'):
            with self.assertRaises(BlockingIOError):
                deck.confirm('26.2.0')
        self.assertTrue(self.pending.exists())


class Metadata(unittest.TestCase):
    def package(self, filename='pool/main/c/chatgpt/chatgpt_26.2.0_amd64.deb', hash='a' * 64):
        return f'Package: chatgpt\nArchitecture: amd64\nVersion: 26.2.0\nFilename: {filename}\nSize: 123\nSHA256: {hash}\n'

    def test_valid_metadata(self):
        self.assertEqual(deck.parse_package(self.package())['Version'], '26.2.0')

    def test_foreign_download_url_rejected(self):
        with self.assertRaises(RuntimeError):
            deck.parse_package(self.package('https://example.org/package.deb'))

    def test_traversal_rejected(self):
        with self.assertRaises(RuntimeError):
            deck.parse_package(self.package('../../file'))

    def test_invalid_hash_rejected(self):
        with self.assertRaises(RuntimeError):
            deck.parse_package(self.package(hash='bad'))

    def test_wrong_architecture_rejected(self):
        with self.assertRaises(RuntimeError):
            deck.parse_package(self.package().replace('amd64', 'arm64'))


if __name__ == '__main__':
    unittest.main()
