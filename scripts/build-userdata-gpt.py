#!/usr/bin/env python3
"""Lay out the payload plus both root windows in one userdata image.

The native UFS driver publishes a window through the partition scheme, so the
root is matched by a GPT partition GUID; a bare file system exposes no identity.
The OS window is therefore written as a GPT disk whose single HFS+ partition
carries the GUID the kernel matches. Both window names are required: the driver
refuses a plan that does not contain `OS` and `SystemOS`.

    python scripts/build-userdata-gpt.py --payload payload.img \
        --os-image root.img --os-guid <the guid board.toml matches> \
        --systemos systemos-raw.img --output userdata.img
"""
import argparse
import hashlib
import pathlib
import struct
import uuid
import zlib

MIB = 1 << 20
SECTOR = 512
CHUNK = 8 << 20
HFS_TYPE = uuid.UUID('48465300-0000-11AA-AA11-00306543ECAC')


def sha256_of(source, size):
    hasher = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = source.read(min(CHUNK, remaining))
        if not chunk:
            raise SystemExit('short source')
        hasher.update(chunk)
        remaining -= len(chunk)
    return hasher.hexdigest()


def copy_exact(source, output, size):
    remaining = size
    while remaining:
        chunk = source.read(min(CHUNK, remaining))
        if not chunk:
            raise SystemExit('short source')
        output.write(chunk)
        remaining -= len(chunk)


def gpt_for(filesystem_bytes, part_guid, label):
    content_sectors = filesystem_bytes // SECTOR
    first_lba = 34
    last_lba = first_lba + content_sectors - 1
    total_lba = last_lba + 34
    entries = bytearray(128 * 128)
    struct.pack_into('<16s16sQQQ72s', entries, 0, HFS_TYPE.bytes_le, part_guid.bytes_le,
                     first_lba, last_lba, 0, label.encode('utf-16-le'))
    entries_crc = zlib.crc32(bytes(entries)) & 0xFFFFFFFF
    disk_guid = uuid.uuid4()

    def header(current, backup):
        block = bytearray(SECTOR)
        struct.pack_into('<8sIIIIQQQQ16sQIII', block, 0, b'EFI PART', 0x00010000, 92, 0, 0,
                         current, backup, 34, total_lba - 34, disk_guid.bytes_le,
                         2, 128, 128, entries_crc)
        # The header CRC covers exactly header_size (92) bytes, not the sector.
        crc = zlib.crc32(bytes(block[:92])) & 0xFFFFFFFF
        struct.pack_into('<I', block, 16, crc)
        return block

    mbr = bytearray(SECTOR)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\x00\x02\x00', 0xEE, b'\xff\xff\xff', 1, total_lba - 1)
    mbr[510:512] = b'\x55\xaa'
    prefix = bytearray(34 * SECTOR)
    prefix[0:SECTOR] = mbr
    prefix[SECTOR:2 * SECTOR] = header(1, total_lba - 1)
    prefix[2 * SECTOR:2 * SECTOR + len(entries)] = entries
    # The backup area is 33 sectors: entries then the header at the last LBA.
    trailer = bytearray(33 * SECTOR)
    trailer[0:len(entries)] = entries
    trailer[32 * SECTOR:33 * SECTOR] = header(total_lba - 1, 1)
    return bytes(prefix), bytes(trailer), total_lba * SECTOR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=pathlib.Path, required=True)
    parser.add_argument('--os-image', type=pathlib.Path, required=True,
                        help='raw HFS+ file system for the OS window')
    parser.add_argument('--os-guid', required=True,
                        help='GPT partition GUID the kernel matches')
    parser.add_argument('--systemos', type=pathlib.Path, required=True)
    parser.add_argument('--offset-mib', type=int, default=1024)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()

    payload = args.payload.read_bytes()
    base = args.offset_mib * MIB
    if len(payload) > base:
        raise SystemExit('the payload does not fit before the root offset')
    os_bytes = args.os_image.stat().st_size
    prefix, trailer, window_bytes = gpt_for(os_bytes, uuid.UUID(args.os_guid), 'iOSRoot')
    systemos_bytes = args.systemos.stat().st_size

    header_at = base
    os_at = base + MIB
    systemos_at = os_at + ((window_bytes + MIB - 1) // MIB) * MIB
    total = systemos_at + ((systemos_bytes + MIB - 1) // MIB) * MIB

    with args.output.open('wb') as output:
        output.write(payload)
        output.write(bytes(base - len(payload)))
        output.write(bytes(MIB))                       # header placeholder
        output.write(prefix)
        with args.os_image.open('rb') as source:
            hasher = hashlib.sha256()
            copy_exact(source, output, os_bytes)
            remaining = os_bytes
        output.write(trailer)
        pad = window_bytes - len(prefix) - os_bytes - len(trailer)
        if pad:
            output.write(bytes(pad))
        with args.systemos.open('rb') as source:
            systemos_digest = sha256_of(source, systemos_bytes)
            source.seek(0)
            copy_exact(source, output, systemos_bytes)
        remaining = total - output.tell()
        if remaining:
            output.write(bytes(remaining))

    # Digest of the whole OS window as written.
    with args.output.open('rb') as written:
        written.seek(os_at)
        os_digest = sha256_of(written, window_bytes)

    header = bytearray(4096)
    header[0:8] = b'SHNGROOT'
    struct.pack_into('<IIQ', header, 8, 1, 2, total)
    struct.pack_into('<32sQQII32s40s', header, 24, b'OS', os_at, window_bytes, 512, 1,
                     bytes.fromhex(os_digest), bytes(40))
    struct.pack_into('<32sQQII32s40s', header, 152, b'SystemOS', systemos_at, systemos_bytes, 512, 1,
                     bytes.fromhex(systemos_digest), bytes(40))
    header[4064:] = hashlib.sha256(header[:4064]).digest()
    with args.output.open('r+b') as output:
        output.seek(header_at)
        output.write(header)

    print(f'userdata: {total} bytes')
    print(f'  OS window      {os_at} .. {os_at + window_bytes} (GPT, guid {args.os_guid})')
    print(f'  SystemOS window {systemos_at} .. {systemos_at + systemos_bytes}')


if __name__ == '__main__':
    main()
