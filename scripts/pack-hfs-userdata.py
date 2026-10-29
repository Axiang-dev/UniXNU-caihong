#!/usr/bin/env python3
"""Wrap a raw HFS+ root image in the SHNGROOT manifest the UFS driver reads.

`build_userdata.py` copies the IPSW members byte for byte. This takes an already
assembled root image instead, so the same partition layout can carry a file system
the IPSW does not ship. The image is copied unchanged and recorded with its digest.

    python scripts/pack-hfs-userdata.py --root root.img --output userdata-hfs.img
"""
import argparse
import hashlib
import json
import pathlib
import struct

MIB = 1 << 20
CHUNK = 8 << 20


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, required=True, help="raw HFS+ image")
    parser.add_argument("--name", default="OS", help="window name recorded in the manifest")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    size = args.root.stat().st_size
    if size == 0:
        raise SystemExit("the root image is empty")
    windows = [dict(name=args.name, path=args.root, offset=MIB,
                    size=size, sha256=sha256_file(args.root))]
    total = MIB + ((size + MIB - 1) // MIB) * MIB

    header = bytearray(4096)
    header[0:8] = b"SHNGROOT"
    struct.pack_into("<IIQ", header, 8, 1, len(windows), total)
    for index, window in enumerate(windows):
        struct.pack_into("<32sQQII32s40s", header, 24 + index * 128,
                         window["name"].encode(), window["offset"], window["size"],
                         512, 1, bytes.fromhex(window["sha256"]), bytes(40))
    header[4064:] = hashlib.sha256(header[:4064]).digest()

    with args.output.open("wb") as output:
        output.write(header)
        output.write(bytes(MIB - len(header)))
        digest = hashlib.sha256()
        with args.root.open("rb") as source:
            for chunk in iter(lambda: source.read(CHUNK), b""):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != windows[0]["sha256"]:
            raise SystemExit("the copy does not match the source digest")
        remaining = total - output.tell()
        if remaining > 0:
            output.write(bytes(remaining))

    report = dict(image_bytes=total, root_sha256=windows[0]["sha256"],
                  windows=[{k: v for k, v in w.items() if k != "path"} for w in windows])
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
