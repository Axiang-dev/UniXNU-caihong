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
import re
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


PERSONALITY_BLOCK = """#if defined(XNU_CAIHONG_PMGR_NUB) && XNU_CAIHONG_PMGR_NUB
    "   ,{"
    "     'IOClass'         = CaihongPMGRN;"
    "     'IOProviderClass' = IOResources;"
    "     'IOResourceMatch' = IOBSD;"
    "     'IOProbeScore'    = 1000:32;"
    "   }"
#endif
"""


def patch_personality(root):
    """Give the caihong PMGR stand-in a personality so IOKit starts it.

    The personality table is added by native-aes-personality.patch, so the block
    has to be a valid diff: every added line needs a "+" prefix and the hunk
    header's new-side count has to grow by the same number of lines.
    """
    target = root / "patches" / "xnu" / "native-aes-personality.patch"
    source = target.read_text()
    if "CaihongPMGRN" in source:
        print(f"{target} already patched")
        return
    lines = [
        "+#if defined(XNU_CAIHONG_PMGR_NUB) && XNU_CAIHONG_PMGR_NUB",
        "+    \"   ,{\"",
        "+    \"     'IOClass'         = CaihongPMGRN;\"",
        "+    \"     'IOProviderClass' = IOPlatformExpertDevice;\"",
        "+    \"     'IOProbeScore'    = 5000:32;\"",
        "+    \"   }\"",
        "+#endif",
    ]
    block = "\n".join(lines) + "\n"
    marker = '     ")";'
    if marker not in source:
        raise SystemExit(f"{target}: personality table end not found")
    source = source.replace(marker, block + marker, 1)

    headers = list(re.finditer(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@", source, re.M))
    if not headers:
        raise SystemExit(f"{target}: no hunk header")
    last = headers[-1]
    fixed = "@@ -%s,%s +%s,%d @@" % (
        last.group(1), last.group(2), last.group(3), int(last.group(4)) + len(lines))
    source = source[:last.start()] + fixed + source[last.end():]
    target.write_text(source)
    print(f"patched {target} (+{len(lines)} lines)")


def patch_personality_cflags(root):
    """Compile the personality only when the board enables it."""
    target = root / "scripts" / "build-xnu-4k-baseline.sh"
    source = target.read_text()
    if "XNU_CAIHONG_PMGR_NUB" in source:
        print(f"{target} already patched")
        return
    marker = 'EXTRA_MAKE_ARGS+=("KernelConfigTables.cpo_CFLAGS_ADD=$PERSONALITY_CFLAGS")'
    if marker not in source:
        raise SystemExit(f"{target}: PERSONALITY_CFLAGS line not found")
    source = source.replace(
        marker,
        'PERSONALITY_CFLAGS="$PERSONALITY_CFLAGS -DXNU_CAIHONG_PMGR_NUB=1"\n' + marker, 1)
    link_marker = '  KERNEL_EXTRA_ARCHIVES="$KERNEL_EXTRA_ARCHIVES ${XNU_EXTRA_LD_ARCHIVES:-}"'
    if link_marker not in source:
        raise SystemExit(f"{target}: KERNEL_EXTRA_ARCHIVES line not found")
    source = source.replace(
        link_marker,
        '  KERNEL_EXTRA_ARCHIVES="$KERNEL_EXTRA_ARCHIVES -Wl,-force_load,$ROOT/build/caihong-pmgr-nub/libCaihongPmgrNub.a"\n' + link_marker, 1)
    target.write_text(source)
    print(f"patched {target}")


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "unixnu")
    patch_source(root)
    patch_probe(root)
    patch_personality(root)
    patch_personality_cflags(root)


if __name__ == "__main__":
    main()
