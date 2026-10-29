#!/usr/bin/env python3
"""Move the root image to its offset inside userdata and put the payload first.

The firmware mounts the payload as FAT16 from the start of userdata and the kernel
reads the root image at `native-ufs-root-offset-mib`, so the two cannot both start
at zero. This shifts an existing root image right in place, which needs only the
offset as extra space, and then writes the payload at the start.

The image must be a bare file system. A SHNGROOT manifest stores absolute window
offsets, so shifting it would leave every entry pointing at the wrong place; build
such an image with build-userdata-gpt.py in one pass instead.

    python scripts/place-root-at-offset.py --image userdata.img --payload payload.img \
        --offset-mib 1024
"""
import argparse
import pathlib

MIB = 1 << 20
CHUNK = 64 << 20


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--payload", type=pathlib.Path, required=True)
    parser.add_argument("--offset-mib", type=int, default=1024)
    args = parser.parse_args()

    payload = args.payload.read_bytes()
    offset = args.offset_mib * MIB
    if len(payload) > offset:
        raise SystemExit("the payload does not fit before the root offset")

    size = args.image.stat().st_size
    with args.image.open("rb") as probe:
        if probe.read(8) == b"SHNGROOT":
            raise SystemExit(
                "this image carries a SHNGROOT manifest: its windows store absolute "
                "offsets, so shifting it would point every entry at the wrong place. "
                "Rebuild it with build-userdata-gpt.py instead."
            )
    target = size + offset
    with args.image.open("r+b") as image:
        image.truncate(target)
        # Walk backwards so the source bytes are read before they are overwritten.
        cursor = size
        while cursor > 0:
            start = max(0, cursor - CHUNK)
            image.seek(start)
            block = image.read(cursor - start)
            if len(block) != cursor - start:
                raise SystemExit(f"short read at {start}")
            image.seek(start + offset)
            image.write(block)
            cursor = start
        image.seek(0)
        image.write(payload)
        image.write(bytes(offset - len(payload)))
    print(f"placed {len(payload)} bytes of payload at 0 and the root at {offset} "
          f"({target} bytes total)")


if __name__ == "__main__":
    main()
