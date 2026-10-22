#!/usr/bin/env python3
"""Adjust two UniXNU sources for a CI build.

1. `scripts/prepare-xnu-source.py` lists Apple headers through api.github.com
   without authentication. GitHub allows 60 anonymous requests per hour per
   address and the shared runner addresses are past that, so the kernel build
   dies with HTTP 403 before compiling. Rewrite it to send GITHUB_TOKEN.

2. `patches/xnu/offline-component-load-probes.patch` calls
   native_legacy_display_probe() without a config guard. The matching archive is
   device specific and this port has no panel backend, so replace the call with
   a comment. The line count stays the same, so the diff headers remain valid.

Usage: python3 patch-xnu-source.py <path-to-unixnu-checkout>
"""
import pathlib
import sys

TOKEN_PATCH = '''import urllib.request

_TOKEN = os.environ.get("GITHUB_TOKEN")
_OPEN = urllib.request.urlopen


def urlopen(url, *args, **kwargs):
    request = urllib.request.Request(url) if isinstance(url, str) else url
    if _TOKEN and getattr(request, "full_url", "").startswith("https://api.github.com/"):
        request.add_header("Authorization", "Bearer " + _TOKEN)
        request.add_header("Accept", "application/vnd.github+json")
    return _OPEN(request, *args, **kwargs)


urllib.request.urlopen = urlopen
'''

UNGUARDED = "+\t\tnative_legacy_display_probe();\n"
REPLACED = "+\t\t/* native_legacy_display_probe() disabled for this port */\n"


def patch_source(root):
    target = root / "scripts" / "prepare-xnu-source.py"
    if not target.is_file():
        raise SystemExit(f"{target} not found")
    source = target.read_text()
    if "_OPEN = urllib.request.urlopen" in source:
        print(f"{target} already patched")
        return
    marker = "import urllib.request\n"
    if marker not in source:
        raise SystemExit(f"{target} does not import urllib.request")
    target.write_text(source.replace(marker, TOKEN_PATCH, 1))
    print(f"patched {target}")


def patch_probe(root):
    target = root / "patches" / "xnu" / "offline-component-load-probes.patch"
    if not target.is_file():
        raise SystemExit(f"{target} not found")
    source = target.read_text()
    if UNGUARDED not in source:
        print(f"{target} already patched or changed upstream")
        return
    target.write_text(source.replace(UNGUARDED, REPLACED, 1))
    print(f"patched {target}")


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "unixnu")
    patch_source(root)
    patch_probe(root)


if __name__ == "__main__":
    main()
