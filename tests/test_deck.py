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


class Startup(unittest.TestCase):
    setUp = Lifecycle.setUp
    def test_primary_detection_flattened_and_nul_arguments(self):
        proc = self.root / 'proc'
        process = proc / '123'
        process.mkdir(parents=True)
        binary = self.versions / '26.2.0/ChatGPT'
        (process / 'exe').symlink_to(binary)
        for args in (b'ChatGPT\0--type=renderer\0', b'ChatGPT --type=gpu-process\0'):
            (process / 'cmdline').write_bytes(args)
            self.assertFalse(deck.primary_running(binary, proc))
        (process / 'cmdline').write_bytes(b'ChatGPT --class=codex-desktop\0')
        self.assertTrue(deck.primary_running(binary, proc))
        self.assertFalse(deck.primary_running(binary, proc, uid=-1))
        self.assertFalse(deck.primary_running(self.versions / '26.1.0/ChatGPT', proc))

    def test_only_one_cold_start_and_pending_timeout(self):
        binary = self.versions / '26.2.0/ChatGPT'
        with patch.object(deck, 'primary_running', return_value=False):
            with deck.startup_gate(binary) as reuse:
                self.assertFalse(reuse)
                with self.assertRaises(RuntimeError):
                    with deck.startup_gate(binary, timeout=0):
                        self.fail('Concurrent cold start allowed')
            with deck.startup_gate(binary) as reuse:
                self.assertFalse(reuse)  # Owner exit releases gate.

    def test_busy_gate_reuses_visible_primary(self):
        binary = self.versions / '26.2.0/ChatGPT'
        with patch.object(deck, 'primary_running', return_value=False):
            with deck.startup_gate(binary):
                with patch.object(deck, 'primary_running', return_value=True):
                    with deck.startup_gate(binary) as reuse:
                        self.assertTrue(reuse)

    def test_duplicate_launch_preserves_args_without_observers(self):
        args = ['chatgpt://open?a=1&b=two', '--example', 'value with spaces']
        with patch.object(deck, 'primary_running', return_value=True), \
                patch.object(deck.subprocess, 'run') as run, \
                patch.object(deck.subprocess, 'Popen') as popen, \
                patch.object(deck, 'confirm') as confirm, \
                patch.object(deck, 'own_window') as window:
            run.return_value.returncode = 0
            self.assertEqual(deck.launch(args), 0)
            self.assertEqual(run.call_args.args[0][-3:], args)
            popen.assert_not_called()
            confirm.assert_not_called()
            window.assert_not_called()
        self.assertTrue(self.pending.exists())

    def test_cli_forwards_launch_flags_unchanged(self):
        with patch.object(deck.sys, 'argv', ['deck', 'launch', '--yes', '--help', 'chatgpt://open']), \
                patch.object(deck, 'launch', return_value=0) as launch:
            self.assertEqual(deck.main(), 0)
            launch.assert_called_once_with(['--yes', '--help', 'chatgpt://open'])

    def test_cold_launch_spawns_once_and_keeps_game_mode_fix(self):
        with patch.object(deck, 'primary_running', return_value=False), \
                patch.dict(deck.os.environ, {'GAMESCOPE_WAYLAND_DISPLAY': 'gamescope-0',
                                            'GTK_IM_MODULE': 'Steam', 'LD_PRELOAD': 'overlay.so'}), \
                patch.object(deck.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = 0
            popen.return_value.wait.return_value = 0
            self.assertEqual(deck.launch(['chatgpt://open']), 0)
            popen.assert_called_once()
            env = popen.call_args.kwargs['env']
            self.assertEqual(env['GTK_IM_MODULE'], 'simple')
            self.assertNotIn('LD_PRELOAD', env)
            self.assertIn('--ozone-platform=x11', popen.call_args.args[0])
        self.assertTrue(self.pending.exists())  # Early exit cannot confirm cleanup.


class ImportAppImage(unittest.TestCase):
    setUp = Lifecycle.setUp

    def source(self):
        image = self.root / 'ChatGPT.AppImage.real'
        image.write_bytes(b'\x7fELF' + b'\0' * 4 + b'AI\x02' + b'hsqs false hsqs')
        return image

    def test_false_magic_is_skipped(self):
        image = self.source()
        with patch.object(deck.subprocess, 'run') as run:
            from types import SimpleNamespace
            run.side_effect = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]
            self.assertEqual(deck.appimage_offset(image), image.read_bytes().rfind(b'hsqs'))

    def test_wrong_hash_never_extracts(self):
        with patch.object(deck, 'run') as run:
            with self.assertRaises(RuntimeError):
                deck.import_appimage(self.source(), '0' * 64)
            run.assert_not_called()

    def test_existing_install_is_preserved(self):
        image = self.source()
        with patch.object(deck.shutil, 'which', return_value='/usr/bin/unsquashfs'), \
                patch.object(deck, 'run') as run:
            with self.assertRaises(RuntimeError):
                deck.import_appimage(image, deck.sha(image))
            run.assert_not_called()
        self.assertEqual(deck.current(), '26.2.0')

    def test_import_keeps_original_and_records_startup_confirmation(self):
        self.pending.unlink()
        deck.CURRENT.unlink()
        image = self.source()
        original_hash = deck.sha(image)

        def extract(args):
            root = Path(args[args.index('-d') + 1]) / 'opt/codex-desktop'
            (root / '.codex-linux').mkdir(parents=True)
            (root / '.codex-linux/build-info.json').write_text(json.dumps(
                {'package_version': '26.3.0', 'package_sha256': 'b' * 64}))
            (root / 'ChatGPT').write_text('extracted app')

        with patch.object(deck.shutil, 'which', return_value='/usr/bin/unsquashfs'), \
                patch.object(deck, 'appimage_offset', return_value=100), \
                patch.object(deck, 'run', side_effect=extract), \
                patch.object(deck, 'validate_payload'):
            deck.import_appimage(image, original_hash)
        self.assertEqual(deck.current(), '26.3.0')
        self.assertEqual(deck.sha(image), original_hash)
        self.assertTrue(self.pending.exists())
        with patch.object(deck, 'legacy_image_in_use', return_value=False):
            deck.confirm('26.3.0')
        self.assertFalse(image.exists())  # Deleted only after confirmed native start.
        self.assertTrue((self.versions / '26.3.0/ChatGPT').exists())
        self.assertFalse(list(self.data.glob('import-*')))

    def test_live_or_changed_legacy_package_is_retained(self):
        image = self.source()
        self.tx.update(legacy_appimage=str(image), legacy_sha256=deck.sha(image))
        deck.atomic_json(self.pending, self.tx)
        with patch.object(deck, 'legacy_image_in_use', return_value=True):
            with self.assertRaises(RuntimeError):
                deck.confirm('26.2.0')
        self.assertTrue(image.exists())
        self.assertTrue(self.pending.exists())
        image.write_text('changed')
        with self.assertRaises(RuntimeError):
            deck.confirm('26.2.0')
        self.assertTrue(image.exists())


if __name__ == '__main__':
    unittest.main()
