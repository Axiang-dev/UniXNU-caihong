#!/usr/bin/env python3
"""Make UniXNU's DriverKit header restore use the workflow token.

prepare-xnu-source.py lists Apple's headers through api.github.com without
authentication. GitHub allows 60 anonymous requests per hour per address, and
the shared runner addresses are well past that, so the kernel build dies with
HTTP 403 before it compiles anything.

This rewrites the script in the UniXNU checkout so api.github.com calls carry
the token from GITHUB_TOKEN. The change is local to the CI workspace.

Usage: python3 patch-xnu-source.py [path-to-unixnu-checkout]
"""
import pathlib
import sys

PATCH = '''import urllib.request

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


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "unixnu")
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
    target.write_text(source.replace(marker, PATCH, 1))
    print(f"patched {target}")


if __name__ == "__main__":
    main()
