#!/usr/bin/env python3
"""Turn IPSW trust caches into the container DarwinBoot validates.

DarwinBoot's ValidTrustCaches() wants a container:

    u32 count
    u32 offset[count]          each 4-byte aligned, >= 4 + count*4
    modules...                 each: u32 version == 2, uuid(16), u32 entries,
                                     entries * 24 bytes (cdhash 20, type 1, pad 3)
    each module: size == 24 + entries*24, cdhashes strictly ascending,
                 type in 1..4

The IPSW hands out two shapes:

    RestoreTrustCache   IM4P-wrapped, then v1: u32 1, uuid(16), u32 count,
                        entries of 22 bytes (cdhash 20, type 1, pad 1)
    StaticTrustCache    IM4P-wrapped, then v2 already (24-byte entries)

So the restore cache needs a v1 -> v2 rewrite, and both need the container.

Usage:
    make-trustcache.py OUTPUT INPUT [INPUT...]

Inputs may be the raw IPSW members (IMG4 wrapped) or already-unwrapped modules.
"""
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from importlib import import_module

unwrap = import_module("unwrap-img4")


def unwrap_img4(data):
    """Return the IM4P payload, or the bytes unchanged when they are not IMG4."""
    if data[:1] != b"\x30":
        return data
    outer, body = unwrap.read_length(data, 1)
    for tag, start, end in unwrap.children(data, body, body + outer):
        if tag == 0x04:
            return data[start:end]
    raise SystemExit("no IM4P payload")


def module_from_v1(blob):
    version, count = struct.unpack_from("<I", blob, 0)[0], struct.unpack_from("<I", blob, 20)[0]
    if version != 1:
        raise SystemExit(f"not a v1 module (version {version})")
    if len(blob) != 24 + count * 22:
        raise SystemExit(f"v1 module is {len(blob)} bytes, expected {24 + count * 22}")
    out = struct.pack("<I", 2) + blob[4:20] + struct.pack("<I", count)
    for i in range(count):
        at = 24 + i * 22
        out += blob[at:at + 20] + bytes([blob[at + 20], 0, 0, 0])
    return out


def module_from_v2(blob):
    version, count = struct.unpack_from("<I", blob, 0)[0], struct.unpack_from("<I", blob, 20)[0]
    if version != 2:
        raise SystemExit(f"not a v2 module (version {version})")
    if len(blob) != 24 + count * 24:
        raise SystemExit(f"v2 module is {len(blob)} bytes, expected {24 + count * 24}")
    return blob


def container(modules):
    count = len(modules)
    base = 4 + count * 4
    offsets, body = [], b""
    for module in modules:
        offsets.append(base + len(body))
        body += module
    out = struct.pack("<I", count) + b"".join(struct.pack("<I", o) for o in offsets) + body
    validate(out)
    return out


def validate(data):
    """Exactly the checks DarwinBoot makes; raises on the first violation."""
    size = len(data)
    count = struct.unpack_from("<I", data, 0)[0]
    if count == 0 or count > 128 or count > (size - 4) // 4:
        raise SystemExit(f"bad container count {count}")
    for i in range(count):
        start = struct.unpack_from("<I", data, 4 + i * 4)[0]
        end = struct.unpack_from("<I", data, 8 + i * 4)[0] if i + 1 < count else size
        if start < 4 + count * 4 or start & 3 or end > size or end < start or end - start < 24:
            raise SystemExit(f"module {i}: bad extent {start:#x}..{end:#x}")
        if struct.unpack_from("<I", data, start)[0] != 2:
            raise SystemExit(f"module {i}: version is not 2")
        entries = struct.unpack_from("<I", data, start + 20)[0]
        if end - start != 24 + entries * 24:
            raise SystemExit(f"module {i}: size {end - start} != 24 + {entries}*24")
        previous = None
        for e in range(entries):
            at = start + 24 + e * 24
            kind = data[at + 20]
            if not 1 <= kind <= 4:
                raise SystemExit(f"module {i} entry {e}: hash type {kind}")
            cdhash = data[at:at + 20]
            if previous is not None and previous >= cdhash:
                raise SystemExit(f"module {i} entry {e}: cdhashes are not ascending")
            previous = cdhash


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    target = pathlib.Path(sys.argv[1])
    modules = []
    for name in sys.argv[2:]:
        blob = unwrap_img4(pathlib.Path(name).read_bytes())
        version = struct.unpack_from("<I", blob, 0)[0]
        modules.append(module_from_v1(blob) if version == 1 else module_from_v2(blob))
        print(f"{pathlib.Path(name).name}: v{version} module, {len(modules[-1])} bytes")
    out = container(modules)
    target.write_bytes(out)
    print(f"{target}: {len(out)} bytes, {len(modules)} cache(s), validated")


if __name__ == "__main__":
    main()
