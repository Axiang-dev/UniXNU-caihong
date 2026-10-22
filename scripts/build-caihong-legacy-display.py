#!/usr/bin/env python3
"""Supply the legacy display archive that the XNU patches reference.

UniXNU's offline-component-load-probes patch calls native_legacy_display_probe()
and native_legacy_display_late_probe() from bsd_init without a config guard, so
the symbols must resolve even though this port has no display backend yet. The
archive is device specific, which is why the base repository does not ship one.

The build script checks build-report.json next to the archive: the archive hash
and the hash of every source it came from. Both are written here.

Run by `unixnu build kernel` through [kernel] archives, with the build tree as
the working directory.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "build" / "native-legacy-display"
SOURCE = OUT / "caihong_legacy_display.c"
OBJECT = OUT / "caihong_legacy_display.o"
ARCHIVE = OUT / "libNativeLegacyDisplay.late.a"

STUB = """/*
 * No legacy display backend on this port yet. The probes are called
 * unconditionally from bsd_init, so they have to exist and do nothing.
 */
void
native_legacy_display_probe(void)
{
}

void
native_legacy_display_late_probe(void)
{
}
"""


def run(*cmd):
    subprocess.run([str(x) for x in cmd], check=True)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    SOURCE.write_text(STUB)
    run(
        "clang",
        "-target", "arm64-apple-ios17.0",
        "-O2",
        "-ffreestanding",
        "-fno-stack-protector",
        "-c", SOURCE,
        "-o", OBJECT,
    )
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    run("ar", "rcs", ARCHIVE, OBJECT)

    report = {
        "archive_sha256": sha256(ARCHIVE),
        "source_sha256": {str(SOURCE.relative_to(ROOT)): sha256(SOURCE)},
    }
    (OUT / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"legacy display stub: {ARCHIVE} ({ARCHIVE.stat().st_size} bytes)")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"{error.cmd[0]} failed with {error.returncode}")
