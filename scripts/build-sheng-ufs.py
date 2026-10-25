#!/usr/bin/env python3
"""Publish the UFS block class under the name the kernel build links.

`build-arm-driver.py NativeUfs` produces build/native-ufs/libNativeUfs.a from
the vendored driver. The kernel build looks for build/sheng-ufs/libShengUfs.a,
because the class started life in the reference port, so compile it with the
shared driver builder and publish it under that name.

Runs as an archive builder (cwd is the build tree, before the final link).
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path.cwd()
SOURCE = ROOT / 'build' / 'native-ufs' / 'libNativeUfs.a'
OUT = ROOT / 'build' / 'sheng-ufs'
TARGET = OUT / 'libShengUfs.a'


def main():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/build-arm-driver.py'), 'NativeUfs'],
                            capture_output=True, text=True, timeout=1800)
    if result.returncode:
        print((result.stdout or '')[-3000:])
        print((result.stderr or '')[-3000:])
        raise SystemExit(f'NativeUfs did not build ({result.returncode})')
    if not SOURCE.is_file():
        raise SystemExit(f'{SOURCE} was not produced')
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    (OUT / 'build-report.json').write_text(json.dumps({'archive_sha256': digest}, indent=2) + '\n')
    print(f'ufs: published {TARGET.relative_to(ROOT)} {TARGET.stat().st_size} bytes')


if __name__ == '__main__':
    main()
