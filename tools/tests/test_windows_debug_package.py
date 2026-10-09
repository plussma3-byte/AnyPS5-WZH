import importlib.util
import contextlib
import json
import struct
import tempfile
import unittest
import zipfile
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path

spec = importlib.util.spec_from_file_location('windows_package', Path(__file__).resolve().parents[1] / 'package_windows_debug.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class WindowsDebugPackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write_results(self, extra=None, omit=None):
        names = sorted(package.REQUIRED_TESTS | {'optional_gpu'})
        (self.root / 'test-inventory.json').write_text(json.dumps({'tests': [{'name': name} for name in names]}))
        suite = ET.Element('testsuite')
        for name in names:
            if name == omit:
                continue
            case = ET.SubElement(suite, 'testcase', name=name, time='0.01')
            if name == 'optional_gpu':
                ET.SubElement(case, 'skipped', message='no Vulkan device')
            if extra and name == extra[0]:
                ET.SubElement(case, extra[1], message='synthetic failure')
        ET.ElementTree(suite).write(self.root / 'ctest.xml', encoding='utf-8')

    def test_skipped_gpu_is_not_a_pass(self):
        self.write_results()
        result = package.summarize(self.root)
        self.assertEqual(result['counts']['skipped'], 1)
        self.assertEqual(result['counts']['passed'], len(package.REQUIRED_TESTS))

    def test_required_skip_and_failure_block_packaging(self):
        for kind in ('skipped', 'failure', 'error'):
            with self.subTest(kind=kind):
                self.write_results(('constant_folding', kind))
                with self.assertRaises(ValueError):
                    package.summarize(self.root)

    def test_missing_result_blocks_packaging(self):
        self.write_results(omit='optional_gpu')
        with self.assertRaises(ValueError):
            package.summarize(self.root)

    def test_pe_rejects_non_windows_and_wrong_architecture(self):
        path = self.root / 'synthetic.exe'
        data = bytearray(256)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 60, 128)
        data[128:132] = b'PE\0\0'
        struct.pack_into('<H', data, 132, 0x8664)
        struct.pack_into('<H', data, 152, 0x20b)
        path.write_bytes(data)
        package.check_pe(path)
        for offset, value in ((132, 0x14c), (152, 0x10b)):
            changed = data.copy()
            struct.pack_into('<H', changed, offset, value)
            path.write_bytes(changed)
            with self.assertRaises(ValueError):
                package.check_pe(path)
        path.write_bytes(b'\x7fELF' + bytes(256))
        with self.assertRaises(ValueError):
            package.check_pe(path)
        path.write_bytes(b'MZ')
        with self.assertRaises(ValueError):
            package.check_pe(path)

    def test_package_contains_logs_symbols_and_matching_hashes(self):
        self.write_results()
        (self.root / 'runner.json').write_text(json.dumps({'OS': 'synthetic fixture'}))
        build = self.root / 'build'
        toolchain = self.root / 'compiler/bin'
        ffmpeg = self.root / 'ffmpeg'
        data = bytearray(256)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 60, 128)
        data[128:132] = b'PE\0\0'
        struct.pack_into('<H', data, 132, 0x8664)
        struct.pack_into('<H', data, 152, 0x20b)
        for path in [build / 'core/relinker/relinker.exe', build / 'core/libs/libs/libc.prx',
                     build / 'core/libs/libs/unpatched/libc.prx', *[toolchain / name for name in package.RUNTIMES]]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.root / 'core/libs/prx/libc').mkdir(parents=True)
        for name in ['LICENSE', 'docs/user/WINDOWS_DEBUG_ARTIFACT.md', 'docs/user/USAGE.md',
                     'docs/user/INPUT_MAPPING.md', 'docs/user/COMPATIBILITY.md',
                     'tools/ci/windows-dependencies.json', 'tools/windows/Capture-Local.ps1',
                     'tools/windows/summarize_frames.py', '3rdparty/LICENSE', 'ffmpeg/share/ffmpeg/copyright']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic packaging fixture')
        with contextlib.chdir(self.root), mock.patch.object(package, 'inspect_imports', return_value=(['KERNEL32.dll'], 'synthetic import listing')):
            package.package(build, self.root, toolchain, ffmpeg, self.root / 'dist', 'a' * 40)
        archive = next((self.root / 'dist').glob('*.zip'))
        with zipfile.ZipFile(archive) as bundle:
            prefix = 'AnyPS5-Windows-x64-Debug-' + 'a' * 12 + '/'
            manifest = json.loads(bundle.read(prefix + 'SHA256.json'))
            self.assertIn('logs/test-summary.json', manifest)
            self.assertIn('symbols/unpatched/libc.prx', manifest)
            self.assertIn('tools/Capture-Local.ps1', manifest)
            for name, digest in manifest.items():
                self.assertEqual(package.hashlib.sha256(bundle.read(prefix + name)).hexdigest(), digest)
            self.assertFalse(json.loads(bundle.read(prefix + 'build-info.json'))['vulkan_runtime_on_target_verified'])
        self.assertTrue(archive.with_suffix('.zip.sha256').read_text().startswith(package.file_digest(archive)))

    def test_unknown_and_path_imports_are_rejected(self):
        names = ['KERNEL32.dll', 'libc.prx', 'api-ms-win-crt-runtime-l1-1-0.dll', 'unexpected.dll', '..\\libc.prx']
        self.assertEqual(package.unresolved_imports(names, {'libc.prx'}), ['unexpected.dll', '..\\libc.prx'])


if __name__ == '__main__':
    unittest.main()
