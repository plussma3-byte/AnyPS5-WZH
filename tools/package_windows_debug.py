import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REQUIRED_TESTS = {'autorun_exit_status', 'buffer_bounds', 'constant_folding', 'param_json_parser', 'agc_validate_program'}
RUNTIMES = ('libgcc_s_seh-1.dll', 'libstdc++-6.dll', 'libwinpthread-1.dll')
SYSTEM_DLLS = {
    'advapi32.dll', 'avrt.dll', 'bcrypt.dll', 'cfgmgr32.dll', 'comctl32.dll',
    'comdlg32.dll', 'crypt32.dll', 'd3d9.dll', 'd3d11.dll', 'dinput8.dll',
    'dsound.dll', 'dwmapi.dll', 'dxgi.dll', 'gdi32.dll', 'hid.dll', 'imm32.dll',
    'iphlpapi.dll', 'kernel32.dll', 'msvcrt.dll', 'ntdll.dll', 'ole32.dll',
    'oleaut32.dll', 'powrprof.dll', 'psapi.dll', 'rpcrt4.dll', 'secur32.dll',
    'setupapi.dll', 'shell32.dll', 'shlwapi.dll', 'ucrtbase.dll', 'user32.dll',
    'userenv.dll', 'version.dll', 'winmm.dll', 'winspool.drv', 'ws2_32.dll',
    'wtsapi32.dll', 'xinput1_4.dll', 'xinput9_1_0.dll',
}


def read_tests(logs):
    inventory = json.loads((logs / 'test-inventory.json').read_text(encoding='utf-8-sig'))
    expected = [item['name'] for item in inventory['tests']]
    if not expected or len(expected) != len(set(expected)):
        raise ValueError('Empty or duplicate registered test names')
    rows = {}
    for case in ET.parse(logs / 'ctest.xml').iter('testcase'):
        name = case.get('name')
        if not name or name in rows:
            raise ValueError('Missing or duplicate JUnit test name')
        failure = case.find('failure')
        error = case.find('error')
        skipped = case.find('skipped')
        if failure is not None or error is not None:
            status = 'failed'
        elif skipped is not None:
            status = 'skipped'
        elif case.get('status') in ('notrun', 'disabled'):
            status = 'incomplete'
        else:
            status = 'passed'
        rows[name] = {'status': status, 'seconds': case.get('time'),
                      'output': case.findtext('system-out', '')}
    return {
        'registered': len(expected),
        'counts': {status: sum(row['status'] == status for row in rows.values())
                   for status in ('passed', 'skipped', 'failed', 'incomplete')},
        'missing': sorted(set(expected) - rows.keys()),
        'unexpected': sorted(rows.keys() - set(expected)),
        'required_not_passed': sorted(name for name in REQUIRED_TESTS if rows.get(name, {}).get('status') != 'passed'),
        'known_unregistered_targets': ['agc_driver_recorder_tests', 'video_out_flip_tests'],
        'tests': rows,
    }


def summarize(logs):
    result = read_tests(logs)
    (logs / 'test-summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    lines = ['# Windows test results', '', f'Registered: {result["registered"]}', '']
    lines += [f'- {key}: {value}' for key, value in result['counts'].items()]
    for key in ('missing', 'unexpected', 'required_not_passed', 'known_unregistered_targets'):
        lines.append(f'- {key}: {", ".join(result[key]) or "none"}')
    lines += ['', 'Skipped tests are not passes. A hosted runner does not validate an RTX 3060 or game compatibility.', '', '## Skipped or incomplete tests', '']
    lines += [f'- `{name}`: {row["status"]}' for name, row in result['tests'].items() if row['status'] != 'passed']
    (logs / 'test-summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if result['counts']['failed'] or result['counts']['incomplete'] or any(result[key] for key in ('missing', 'unexpected', 'required_not_passed')):
        raise ValueError('Test gate failed; see test-summary.json')
    return result


def check_pe(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing or symlinked binary: {path}')
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError(f'Not a PE executable: {path}')
        offset = struct.unpack_from('<I', header, 60)[0]
        if offset < 64 or offset > path.stat().st_size - 26:
            raise ValueError(f'Invalid PE header offset: {path}')
        stream.seek(offset)
        pe = stream.read(26)
    if pe[:4] != b'PE\0\0' or struct.unpack_from('<H', pe, 4)[0] != 0x8664 or struct.unpack_from('<H', pe, 24)[0] != 0x20b:
        raise ValueError(f'Expected AMD64 PE32+: {path}')


def inspect_imports(path, objdump):
    result = subprocess.run([str(objdump), '-p', str(path)], check=True, capture_output=True, text=True, errors='replace')
    names = re.findall(r'^\s*DLL Name:\s*(\S+)\s*$', result.stdout, flags=re.MULTILINE)
    if re.search(r'DLL Name:', result.stdout) and not names:
        raise ValueError(f'Unrecognized import listing: {path}')
    return names, result.stdout


def unresolved_imports(names, bundled):
    missing = []
    for name in names:
        lower = name.lower()
        if lower in bundled or lower in SYSTEM_DLLS:
            continue
        if re.fullmatch(r'(api-ms-win-|ext-ms-win-)[a-z0-9-]+\.dll', lower):
            continue
        missing.append(name)
    return missing


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_file(source, destination):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'Missing or symlinked package input: {source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def package(build, logs, toolchain, ffmpeg, output, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Expected the exact 40-character source commit')
    summary = summarize(logs)
    expected = {p.name + '.prx' for p in Path('core/libs/prx').iterdir() if p.is_dir()}
    runtime = build / 'core/libs/libs'
    found = {p.name for p in runtime.glob('*.prx')}
    if found != expected:
        raise ValueError(f'Patched library mismatch: missing={sorted(expected-found)}, unexpected={sorted(found-expected)}')
    root = output / f'AnyPS5-Windows-x64-Debug-{commit[:12]}'
    if root.exists():
        raise ValueError('Package staging directory already exists')
    binaries = [(build / 'core/relinker/relinker.exe', Path('relinker.exe'))]
    binaries += [(runtime / name, Path('libs') / name) for name in sorted(expected)]
    binaries += [(toolchain / name, Path('libs') / name) for name in RUNTIMES]
    bundled = {target.name.lower() for _, target in binaries}
    imports = {}
    dump = []
    for source, target in binaries:
        check_pe(source)
        names, listing = inspect_imports(source, toolchain / 'objdump.exe')
        imports[str(target)] = {'imports': names, 'unresolved': unresolved_imports(names, bundled)}
        dump.append(f'FILE: {target}\n{listing}')
    (logs / 'pe-imports.json').write_text(json.dumps(imports, indent=2), encoding='utf-8')
    (logs / 'objdump.txt').write_text('\n'.join(dump), encoding='utf-8')
    if any(item['unresolved'] for item in imports.values()):
        raise ValueError('Unresolved non-system DLL imports; see pe-imports.json')
    for source, target in binaries:
        copy_file(source, root / target)
    for name in RUNTIMES:
        copy_file(toolchain / name, root / name)
    for name in sorted(expected):
        source = runtime / 'unpatched' / name
        check_pe(source)
        copy_file(source, root / 'symbols/unpatched' / name)
    for source in logs.iterdir():
        if source.is_file() and source.name != 'package.log':
            copy_file(source, root / 'logs' / source.name)
    last_test = build / 'Testing/Temporary/LastTest.log'
    if last_test.is_file():
        copy_file(last_test, root / 'logs/LastTest.log')
    copy_file(Path('LICENSE'), root / 'licenses/AnyPS5-LICENSE')
    copy_file(Path('docs/user/WINDOWS_DEBUG_ARTIFACT.md'), root / 'README.md')
    for name in ('USAGE.md', 'INPUT_MAPPING.md', 'COMPATIBILITY.md'):
        copy_file(Path('docs/user') / name, root / 'docs' / name)
    copy_file(Path('tools/ci/windows-dependencies.json'), root / 'dependencies.json')
    for name in ('Capture-Local.ps1', 'summarize_frames.py'):
        copy_file(Path('tools/windows') / name, root / 'tools' / name)
    license_sources = [Path('3rdparty'), ffmpeg / 'share/ffmpeg', toolchain.parent]
    for index, source in enumerate(license_sources):
        if not source.is_dir():
            raise ValueError(f'Missing license source: {source}')
        for file in source.rglob('*'):
            if file.is_file() and not file.is_symlink() and re.match(r'^(LICENSE|COPYING|COPYRIGHT|AUTHORS)([._-]|$)', file.name, re.IGNORECASE):
                copy_file(file, root / 'licenses' / str(index) / file.relative_to(source))
    metadata = {'commit': commit, 'configuration': 'Debug', 'architecture': 'x64',
                'test_counts': summary['counts'], 'games_executed': False,
                'vulkan_runtime_on_target_verified': False,
                'inspection': 'PE headers and static DLL imports; not malware certification or dynamic-load coverage',
                'runner': json.loads((logs / 'runner.json').read_text(encoding='utf-8-sig'))}
    (root / 'build-info.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    files = {p.relative_to(root).as_posix(): file_digest(p)
             for p in sorted(root.rglob('*')) if p.is_file()}
    (root / 'SHA256.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    archive = output / (root.name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('ZIP verification failed')
    archive.with_suffix('.zip.sha256').write_text(file_digest(archive) + '  ' + archive.name + '\n', encoding='ascii')
    print(f'Created {archive}; tests skipped={summary["counts"]["skipped"]}; no games executed')


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)
    report = subparsers.add_parser('summarize')
    report.add_argument('--logs', type=Path, required=True)
    bundle = subparsers.add_parser('package')
    for name in ('build', 'logs', 'toolchain', 'ffmpeg', 'output'):
        bundle.add_argument('--' + name, type=Path, required=True)
    bundle.add_argument('--commit', required=True)
    args = parser.parse_args()
    if args.command == 'summarize':
        summarize(args.logs)
    else:
        package(args.build, args.logs, args.toolchain, args.ffmpeg, args.output, args.commit)


if __name__ == '__main__':
    main()
