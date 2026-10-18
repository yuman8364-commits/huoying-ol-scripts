"""Packaging evidence tests; all archives and runtime trees are local fixtures."""
import marshal
from pathlib import Path
import tempfile
import unittest

from tools.release_runtime_snapshot import capture, verify
from tools.verify_packaged_source import normalized_code, verify_package


class PackagedSourceTests(unittest.TestCase):
    def fixture_archive(self, source, *, entrypoint=True, extras=None, entry_name='hyol_fixture'):
        code = compile(source, '/old-build/entry.py', 'exec')
        modules = {entry_name: code, **(extras or {})}
        class Pyz:
            toc = modules
            def extract(self, module):
                return modules[module]
        class Archive:
            toc = {entry_name: (0, 0, 0, 0, 's')} if entrypoint else {}
            def open_embedded_archive(self, name):
                self.name = name
                return Pyz()
            def extract(self, module):
                return marshal.dumps(code)
        return Archive()

    def test_filename_difference_does_not_hide_equal_nested_code(self):
        source = 'def function(x):\n    return lambda: x + 13\n'
        self.assertEqual(normalized_code(compile(source, 'old.py', 'exec')),
                         normalized_code(compile(source, 'new.py', 'exec')))

    def test_changed_nested_constant_is_detected(self):
        first = compile('def function():\n    return 13\n', 'first.py', 'exec')
        second = compile('def function():\n    return 14\n', 'second.py', 'exec')
        self.assertNotEqual(normalized_code(first), normalized_code(second))

    def test_frozen_set_constant_order_is_canonical(self):
        original = compile('x = 1\n', 'original.py', 'exec')
        first = original.replace(co_consts=(frozenset(['one', 'two']),))
        second = original.replace(co_consts=(frozenset(['two', 'one']),))
        self.assertEqual(normalized_code(first), normalized_code(second))

    def test_both_pyz_and_entrypoint_are_checked_against_actual_source(self):
        source = 'VALUE = 13\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'hyol_fixture.py'
            path.write_text(source, encoding='utf-8')
            archive = self.fixture_archive(source)
            result = verify_package(folder, 'fixture.exe', archive_factory=lambda _: archive)
            self.assertTrue(result['source_code_matches'])
            self.assertEqual({'PYZ', 'entrypoint'}, {row['location'] for row in result['checks']})
            path.write_text('VALUE = 14\n', encoding='utf-8')
            result = verify_package(folder, 'fixture.exe', archive_factory=lambda _: archive)
            self.assertFalse(result['source_code_matches'])
            self.assertTrue(all(not row['code_matches_current_source'] for row in result['checks']))

    def test_unknown_project_module_is_not_silently_skipped(self):
        source = 'VALUE = 13\n'
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'hyol_fixture.py').write_text(source, encoding='utf-8')
            archive = self.fixture_archive(source, extras={'hyol_missing': compile('x=1', 'missing.py', 'exec')})
            result = verify_package(folder, 'fixture.exe', archive_factory=lambda _: archive)
            self.assertFalse(result['source_code_matches'])
            self.assertFalse(next(row for row in result['checks'] if row['module'] == 'hyol_missing')['source_exists'])

    def test_missing_entrypoint_rejects_an_incomplete_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'hyol_fixture.py').write_text('x=1', encoding='utf-8')
            archive = self.fixture_archive('x=1', entrypoint=False)
            with self.assertRaisesRegex(ValueError, 'No project entrypoint'):
                verify_package(folder, 'fixture.exe', archive_factory=lambda _: archive)

    def test_daruma_package_init_and_entrypoint_are_checked_and_stale_module_fails(self):
        source = 'VALUE = 13\n'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'daruma_standalone').mkdir()
            (root/'daruma_standalone_gui.py').write_text(source, encoding='utf-8')
            init = root/'daruma_standalone/__init__.py'
            init.write_text('VERSION = "new"\n', encoding='utf-8')
            archive = self.fixture_archive(source, entry_name='daruma_standalone_gui',
                extras={'daruma_standalone': compile('VERSION = "new"\n', 'old/__init__.py', 'exec')})
            result = verify_package(root, 'fixture.exe', archive_factory=lambda _: archive)
            self.assertTrue(result['source_code_matches'])
            self.assertEqual({'daruma_standalone', 'daruma_standalone_gui'},
                             {row['module'] for row in result['checks']})
            init.write_text('VERSION = "newer"\n', encoding='utf-8')
            result = verify_package(root, 'fixture.exe', archive_factory=lambda _: archive)
            self.assertFalse(result['source_code_matches'])
            self.assertFalse(next(row for row in result['checks']
                                  if row['module'] == 'daruma_standalone')['code_matches_current_source'])

    def test_same_named_runtime_data_directory_does_not_hide_the_python_module(self):
        from tools.verify_packaged_source import compare_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'daruma_standalone/runtime').mkdir(parents=True)
            (root/'daruma_standalone/runtime.py').write_text('VALUE = 13\n', encoding='utf-8')
            row = compare_source(root, 'daruma_standalone.runtime',
                                 compile('VALUE = 13\n', 'old/runtime.py', 'exec'))
            self.assertTrue(row['source_exists'])
            self.assertTrue(row['code_matches_current_source'])


class RuntimePreservationTests(unittest.TestCase):
    def fixture_tree(self, folder):
        root, desktop = Path(folder)/'project', Path(folder)/'desktop'
        package = desktop/'可用脚本'/'火影OL日常任务'
        files = [package/'state'/'2026-10-04'/'pending.json', package/'logs'/'trace.jsonl',
                 package/'daily_all_result.json']
        for index, path in enumerate(files):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('original '+str(index)).encode())
        return root, desktop, package, files

    def test_only_runtime_files_are_snapshotted_and_original_bytes_are_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root, desktop, package, files = self.fixture_tree(folder)
            (package/'client.exe').write_bytes(b'old executable')
            (package/'DAILY_Client.ini').write_bytes(b'configuration')
            original = {str(path): path.read_bytes() for path in files}
            snapshot = capture(root, desktop)
            self.assertEqual(3, snapshot['runtime_files'])
            self.assertEqual('PASS', verify(snapshot)['result'])
            self.assertEqual(original, {str(path): path.read_bytes() for path in files})

    def test_deleted_pending_and_changed_log_fail_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            root, desktop, package, files = self.fixture_tree(folder)
            snapshot = capture(root, desktop)
            files[0].unlink()
            files[1].write_bytes(b'overwritten')
            result = verify(snapshot)
            self.assertEqual('FAIL', result['result'])
            self.assertEqual([str(files[0].resolve())], result['missing'])
            self.assertEqual([str(files[1].resolve())], result['changed'])

    def test_new_file_is_reported_without_discarding_original_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root, desktop, package, files = self.fixture_tree(folder)
            snapshot = capture(root, desktop)
            new = package/'logs'/'new.jsonl'
            new.write_bytes(b'new evidence')
            result = verify(snapshot)
            self.assertEqual('PASS', result['result'])
            self.assertEqual([str(new.resolve())], result['added'])
            self.assertEqual(3, result['runtime_files_checked'])


if __name__ == '__main__':
    unittest.main()
