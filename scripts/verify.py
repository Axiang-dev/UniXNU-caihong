#!/usr/bin/env python3
"""Check that this repository is self-consistent enough for someone else to use.

Run locally (`python scripts/verify.py`) or by CI on every push. It checks the
things that actually broke during the port: stale build output, references to
files that do not exist, configuration the build silently depends on, and the
trust cache conversion (which is easy to get subtly wrong).
"""
import ast
import pathlib
import re
import struct
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
problems = []
notes = []


def fail(message):
    problems.append(message)


def ok(message):
    notes.append(message)


# --- every Python file parses ------------------------------------------------
for path in sorted(ROOT.rglob("*.py")):
    if ".git" in path.parts:
        continue
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        fail(f"{path.relative_to(ROOT)}: syntax error: {exc}")
ok(f"{len(list(ROOT.rglob('*.py')))} python files parse")

# --- board.toml -------------------------------------------------------------
board_path = ROOT / "board.toml"
if not board_path.is_file():
    fail("board.toml is missing")
    board = {}
else:
    board = tomllib.loads(board_path.read_text(encoding="utf-8"))

payload = board.get("payload", {})
for key in ("volume", "size_mib", "loader", "title", "dram_size", "kernel_args"):
    if key not in payload:
        fail(f"board.toml: [payload] has no {key}")
if payload.get("volume") and len(str(payload["volume"])) > 11:
    fail(f"board.toml: volume label {payload['volume']!r} is longer than the FAT limit of 11")

adt = payload.get("adt", {})
tool = adt.get("tool")
if tool and not (ROOT / tool).is_file():
    fail(f"board.toml: adt.tool {tool!r} does not exist")
elif tool:
    ok(f"board.toml: adt.tool {tool} exists")

ipsw = board.get("ipsw", {})
if not ipsw.get("url", "").startswith("https://"):
    fail("board.toml: [ipsw] url is missing or not https")
if not ipsw.get("fetch"):
    fail("board.toml: [ipsw] fetch lists no components")

userdata = board.get("userdata", {})
first, last = userdata.get("first_lba", 0), userdata.get("last_lba", 0)
if not first or not last or last <= first:
    fail(f"board.toml: [userdata] extent looks wrong ({first}..{last}); "
         "read it from the live GPT, never guess")
else:
    capacity = (last - first + 1) * userdata.get("lba_bytes", 4096)
    ok(f"board.toml: userdata is {capacity / 2**30:.0f} GiB")
if userdata.get("lba_bytes") != 4096:
    fail(f"board.toml: lba_bytes is {userdata.get('lba_bytes')}, expected 4096")

# --- scripts referenced from the workflow exist -----------------------------
workflow = ROOT / ".github" / "workflows" / "kernel.yml"
if not workflow.is_file():
    fail("the kernel workflow is missing")
else:
    text = workflow.read_text(encoding="utf-8")
    for match in re.finditer(r"\.\./device/([A-Za-z0-9_./-]+\.py)", text):
        target = ROOT / match.group(1)
        if not target.is_file():
            fail(f"workflow calls ../device/{match.group(1)} which does not exist")
    ok("workflow script references resolve")

# --- README references ------------------------------------------------------
readme = (ROOT / "README.md").read_text(encoding="utf-8")
for match in re.finditer(r"`((?:scripts|configs|uefi-overlay|patches)/[A-Za-z0-9_./-]+)`", readme):
    name = match.group(1)
    if not (ROOT / name).exists():
        # the reference port's patch is documented as living elsewhere
        if name.endswith("giulia-unixnu.patch"):
            continue
        fail(f"README references {name}, which does not exist")
ok("README file references resolve")

# --- the firmware overlay and patches are present and well formed ----------
overlay = ROOT / "uefi-overlay"
overlay_files = [f for f in overlay.rglob("*") if f.is_file() and f.name != "README.md"]
if len(overlay_files) < 5:
    fail(f"uefi-overlay has only {len(overlay_files)} files; the firmware side looks incomplete")
for path in overlay_files:
    if path.stat().st_size == 0:
        fail(f"{path.relative_to(ROOT)} is empty")
ok(f"uefi-overlay carries {len(overlay_files)} files")

patch = ROOT / "patches" / "uefi-integration.patch"
if not patch.is_file():
    fail("patches/uefi-integration.patch is missing")
else:
    head = patch.read_text(encoding="utf-8", errors="replace")[:200]
    if not head.startswith("diff --git"):
        fail("patches/uefi-integration.patch does not start with a git diff")
    touched = patch.read_text(encoding="utf-8", errors="replace").count("diff --git")
    if touched < 10:
        fail(f"patches/uefi-integration.patch touches only {touched} files")
    else:
        ok(f"patches/uefi-integration.patch touches {touched} files")

# --- no build output, no secrets -------------------------------------------
for path in ROOT.rglob("*"):
    if ".git" in path.parts or not path.is_file():
        continue
    size = path.stat().st_size
    if size > 2 * 1024 * 1024:
        fail(f"{path.relative_to(ROOT)} is {size / 2**20:.1f} MiB: build output does not belong here")
    if path.suffix in (".img", ".macho", ".bin") and "uefi-overlay" not in path.parts:
        fail(f"{path.relative_to(ROOT)} looks like build output")

for path in ROOT.rglob("*"):
    if ".git" in path.parts or not path.is_file() or path.stat().st_size > 512 * 1024:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for pattern in (r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"AKIA[0-9A-Z]{16}"):
        if re.search(pattern, text):
            fail(f"{path.relative_to(ROOT)} looks like it contains a credential")
ok("no credentials or oversized files")

# --- trust cache conversion self-test --------------------------------------
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("make_trustcache", ROOT / "scripts" / "make-trustcache.py")
make_trustcache = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_trustcache)

entries = [(bytes([i]) + bytes(19), 2) for i in range(1, 6)]  # ascending cdhashes
v1 = struct.pack("<I", 1) + bytes(16) + struct.pack("<I", len(entries))
for cdhash, kind in entries:
    v1 += cdhash + bytes([kind, 0])
module = make_trustcache.module_from_v1(v1)
if struct.unpack_from("<I", module, 0)[0] != 2:
    fail("trust cache: v1 -> v2 conversion did not set version 2")
if len(module) != 24 + len(entries) * 24:
    fail(f"trust cache: converted module is {len(module)} bytes, expected {24 + len(entries) * 24}")
built = make_trustcache.container([module])
if len(built) != 4 + 4 + len(module):
    fail("trust cache: container size is wrong")
try:
    make_trustcache.validate(built)
except SystemExit as exc:
    fail(f"trust cache: container does not validate: {exc}")
ok("trust cache conversion self-test passes")

# --- userdata layout self-test ---------------------------------------------
# The window offsets in the SHNGROOT header are absolute. An image whose header
# points a megabyte past its data only fails on the tablet, as "Failed to mount
# root device", so build a small one here and read the entries back.
import subprocess  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = pathlib.Path(tmpdir)
    (tmp / "payload.img").write_bytes(bytes(512 << 10))
    (tmp / "root.img").write_bytes(b"HX" + bytes(1) * ((8 << 20) - 2))
    (tmp / "sysos.img").write_bytes(b"\x1a\x3a\x25\x60" + bytes(2) * ((3 << 20) - 4))
    built = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build-userdata-gpt.py"),
         "--payload", str(tmp / "payload.img"),
         "--os-image", str(tmp / "root.img"),
         "--os-guid", "366B077F-A4FA-35DF-9A9C-B6A5CAFC8F7E",
         "--systemos", str(tmp / "sysos.img"),
         "--offset-mib", "1",
         "--output", str(tmp / "out.img")],
        capture_output=True, text=True)
    if built.returncode != 0:
        fail(f"userdata layout: the builder failed: {built.stderr.strip().splitlines()[-1:]}")
    else:
        image = (tmp / "out.img").read_bytes()
        header = image[1 << 20:(1 << 20) + 4096]
        if header[:8] != b"SHNGROOT":
            fail("userdata layout: no manifest at the header offset")
        else:
            name, os_at, *_ = struct.unpack_from("<32sQQII32s40s", header, 24)
            name, sysos_at, *_ = struct.unpack_from("<32sQQII32s40s", header, 152)
            if image[os_at + 512:os_at + 520] != b"EFI PART":
                fail(f"userdata layout: OS window at {os_at} has no GPT header")
            if image[sysos_at:sysos_at + 4] != b"\x1a\x3a\x25\x60":
                fail(f"userdata layout: SystemOS window at {sysos_at} does not hold its data")
            if not (os_at and sysos_at):
                fail("userdata layout: a window offset is zero")
            ok("userdata layout self-test passes")

# --- report -----------------------------------------------------------------
for line in notes:
    print(f"  ok   {line}")
for line in problems:
    print(f"  FAIL {line}")
print()
if problems:
    print(f"{len(problems)} problem(s)")
    raise SystemExit(1)
print("all checks passed")
