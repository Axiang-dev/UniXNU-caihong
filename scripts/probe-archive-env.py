#!/usr/bin/env python3
"""Report what an archive builder can see, then do nothing.

Archive builders run after the bootstrap pass and before the final link, which is
the only window where a prelink candidate can be produced for a builder that
needs one. This prints the paths that window has to work with so the display
builder can be written against reality instead of guesses.
"""
import os
import pathlib

lines = []

ROOT = pathlib.Path.cwd()
lines.append(f'archive env: cwd={ROOT}')

for label, pattern in (
        ('board.toml', 'board.toml'),
        ('emit_prelink', 'kits/NativePrelink/emit_prelink.py'),
        ('native_prelink', 'kits/NativePrelink/native_prelink.py'),
        ('bootstrap kernel', 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf'),
        ('compile record', 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json'),
        ('drivers dir', 'drivers/NativeMobileDisplay'),
):
    path = ROOT / pattern
    lines.append(f'  {label:18s} {pattern} -> {"yes" if path.exists() else "NO"}')

lines.append('  kernelcache search:')
for pattern in ('.cache/**/kernelcache*', '**/kernelcache.decompressed', 'refer/**/*.decompressed',
                '.cache/**/*.ipsw', 'build/**/kernelcache*'):
    for hit in sorted(ROOT.glob(pattern))[:8]:
        lines.append(f'    {pattern}: {hit.relative_to(ROOT)}  {hit.stat().st_size} bytes')
for base in ('.cache', 'refer'):
    directory = ROOT / base
    if directory.is_dir():
        lines.append(f'  {base}/: ' + ', '.join(sorted(p.name for p in directory.iterdir())[:12]))

lines.append('  top-level entries: ' + ', '.join(sorted(p.name for p in ROOT.iterdir()))[:400])
lines.append('  env: ' + str({k: v for k, v in os.environ.items() if k.startswith('UNIXNU') or k.startswith('XNU_')}))

report = '\n'.join(lines)
(ROOT / 'archive-env.txt').write_text(report + '\n', encoding='utf-8')
summary = os.environ.get('GITHUB_STEP_SUMMARY')
if summary:
    with open(summary, 'a', encoding='utf-8') as handle:
        handle.write('```\n' + report + '\n```\n')
print(report)
