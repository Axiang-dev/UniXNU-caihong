#!/usr/bin/env python3
"""Report what an archive builder can see, then do nothing.

Archive builders run after the bootstrap pass and before the final link, which is
the only window where a prelink candidate can be produced for a builder that
needs one. This prints the paths that window has to work with so the display
builder can be written against reality instead of guesses.
"""
import os
import pathlib

ROOT = pathlib.Path.cwd()
print(f'archive env: cwd={ROOT}')

for label, pattern in (
        ('board.toml', 'board.toml'),
        ('emit_prelink', 'kits/NativePrelink/emit_prelink.py'),
        ('native_prelink', 'kits/NativePrelink/native_prelink.py'),
        ('bootstrap kernel', 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf'),
        ('compile record', 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json'),
        ('drivers dir', 'drivers/NativeMobileDisplay'),
):
    path = ROOT / pattern
    print(f'  {label:18s} {pattern} -> {"yes" if path.exists() else "NO"}')

print('  ipsw kernelcache candidates:')
for base in ('out', '../out', '/tmp'):
    directory = (ROOT / base)
    if not directory.is_dir():
        continue
    for hit in sorted(directory.glob('ipsw/**/kernelcache*'))[:6]:
        print(f'    {hit}  {hit.stat().st_size} bytes')

print('  top-level entries:', ', '.join(sorted(p.name for p in ROOT.iterdir()))[:400])
print('  UNIXNU_* env:', {k: v for k, v in os.environ.items() if k.startswith('UNIXNU') or k.startswith('XNU_')})
