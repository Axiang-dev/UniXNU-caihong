#!/usr/bin/env python3
"""Strip the IMG4 wrapper from an IPSW component.

Several IPSW members are signed IMG4 payloads rather than the raw image their
name suggests: the restore ramdisk is IM4P, so hdiutil reports "image not
recognized". The DER structure is

    SEQUENCE {
      IA5String  "IM4P"
      IA5String  description
      OCTET STRING payload      <- the real image
      OCTET STRING keybag       (optional)
      ...
    }

Usage: unwrap-img4.py INPUT OUTPUT
"""
import pathlib
import sys


def read_length(data, at):
    first = data[at]
    if first < 0x80:
        return first, at + 1
    count = first & 0x7F
    return int.from_bytes(data[at + 1:at + 1 + count], "big"), at + 1 + count


def children(data, start, end):
    at = start
    while at < end:
        tag = data[at]
        length, body = read_length(data, at + 1)
        yield tag, body, body + length
        at = body + length


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    source = pathlib.Path(sys.argv[1])
    target = pathlib.Path(sys.argv[2])
    data = source.read_bytes()

    if data[:1] != b"\x30":
        # Already a plain image; copy it through.
        target.write_bytes(data)
        print(f"{source.name}: not IMG4, copied as is")
        return

    outer, body = read_length(data, 1)
    items = list(children(data, body, body + outer))
    if not items or data[items[0][1]:items[0][2]] != b"IM4P":
        raise SystemExit(f"{source.name}: no IM4P payload")

    # The first OCTET STRING after the two strings is the image itself.
    for tag, start, end in items:
        if tag == 0x04:
            payload = data[start:end]
            target.write_bytes(payload)
            print(f"{source.name}: IM4P payload {len(payload)} bytes -> {target.name}")
            return
    raise SystemExit(f"{source.name}: no payload string")


if __name__ == "__main__":
    main()
