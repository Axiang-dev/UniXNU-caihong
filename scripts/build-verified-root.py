#!/usr/bin/env python3
"""Publish the signed root policy as the archive the kernel links.

The policy in `policy/os` was produced by the VerifiedRoot manifest tool from
this board's OS window and accepted by `vr_open` in C: key anchor, RSA-3072
header signature and hash table. This stages it where UniXNU's
`build-verified-root-probe.py` expects it and runs that script, which compiles
the kit's core, its XNU crypto adapter and the embedded policy into
`build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a`.

The policy is committed instead of rebuilt here because it only changes when the
target IPSW changes, and rebuilding it needs the 5 GB OS member.
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path.cwd()
# `make-owner-policy.py` produces the policy in the build tree; a committed copy
# under policy/os is used when there is one.
GENERATED = ROOT / 'build' / 'verified-root-os'
COMMITTED = ROOT / 'policy' / 'os'
SOURCE = GENERATED if (GENERATED / 'manifest.bin').is_file() else COMMITTED
STAGE = GENERATED
OUT = ROOT / 'build' / 'verified-root-os-xnu-bundle' / 'libVerifiedRootProbe.a'


def main():
    if not (SOURCE / 'manifest.bin').is_file():
        raise SystemExit(f'{SOURCE} has no manifest.bin')
    STAGE.mkdir(parents=True, exist_ok=True)
    for name in ('manifest.bin', 'trusted-key.bin', 'verification.json', 'original-samples.bin'):
        if not (SOURCE / name).is_file():
            raise SystemExit(f'{SOURCE / name} is missing')
        if SOURCE.resolve() != STAGE.resolve():
            shutil.copyfile(SOURCE / name, STAGE / name)
    record = json.loads((STAGE / 'verification.json').read_text())
    print(f"verified root: {record['blocks_verified']} blocks, image {record['image_bytes']} bytes, "
          f"uuid {record['volume_uuid']}")
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/build-verified-root-probe.py'),
                             '--component', 'os'],
                            capture_output=True, text=True, timeout=3600)
    if result.returncode:
        print((result.stdout or '')[-3000:])
        print((result.stderr or '')[-3000:])
        raise SystemExit(f'the verified root archive did not build ({result.returncode})')
    if not OUT.is_file():
        raise SystemExit(f'{OUT} was not produced')
    print(f"verified root: published {OUT.relative_to(ROOT)} {OUT.stat().st_size} bytes")


if __name__ == '__main__':
    main()
