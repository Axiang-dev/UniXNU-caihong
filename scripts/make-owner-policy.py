#!/usr/bin/env python3
"""Build the signed OS policy on the runner, from the IPSW's OS member.

The policy is derived data and the repository refuses blobs, so it is produced
here. The OS member is attached read-only and read through the raw device, so
nothing is copied and no expanded image is left behind.

Output matches what `build-verified-root-probe.py` expects in
`build/verified-root-os`: manifest.bin, trusted-key.bin, verification.json and
original-samples.bin.
"""
import argparse
import hashlib
import json
import os
import pathlib
import plistlib
import re
import struct
import subprocess
import sys
import uuid

BLOCK = 4096
OS_BYTES = 6077580800
OS_BLOCKS = 1483785
OS_UUID = '04769D26-2CA0-4825-B443-E2E5F88300F2'
SHA256_DIGEST_INFO = bytes.fromhex('3031300d060960864801650304020105000420')


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def attach(image):
    out = subprocess.run(['hdiutil', 'attach', '-nomount', '-readonly', '-noverify', '-noautofsck', str(image)],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        fields = line.split()
        if fields and fields[0].startswith('/dev/disk'):
            return fields[0]
    raise SystemExit('hdiutil did not report a device')


def detach(device):
    subprocess.run(['hdiutil', 'detach', device], capture_output=True)


def rsa_verify(key, digest, signature):
    modulus = int.from_bytes(key[8:392], 'big')
    exponent = int.from_bytes(key[392:395], 'big')
    value = pow(int.from_bytes(signature, 'big'), exponent, modulus)
    block = value.to_bytes(384, 'big')
    expected = b'\x00\x01' + b'\xff' * (384 - 3 - len(SHA256_DIGEST_INFO) - 32) + b'\x00' + SHA256_DIGEST_INFO + digest
    return block == expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=pathlib.Path, help='the OS member of the IPSW (default: $XNU_OS_MEMBER)')
    parser.add_argument('--output', type=pathlib.Path, default=pathlib.Path('build/verified-root-os'))
    parser.add_argument('--manifest-tool', type=pathlib.Path, default=pathlib.Path('kits/VerifiedRoot/tools/manifest.py'))
    parser.add_argument('--keys', type=pathlib.Path, default=pathlib.Path('build/verified-root-keys'))
    args = parser.parse_args()

    if args.image is None:
        from_env = os.environ.get('XNU_OS_MEMBER')
        if not from_env:
            raise SystemExit('pass --image or set XNU_OS_MEMBER to the OS member of the IPSW')
        args.image = pathlib.Path(from_env)
    if not args.image.is_file():
        raise SystemExit(f'{args.image} is not a file')
    args.output.mkdir(parents=True, exist_ok=True)
    args.keys.mkdir(parents=True, exist_ok=True)
    private_key, public_key = args.keys / 'owner.pem', args.keys / 'owner-public.pem'
    if not private_key.is_file():
        subprocess.run(['openssl', 'genrsa', '-out', str(private_key), '3072'], check=True, capture_output=True)
        subprocess.run(['openssl', 'rsa', '-in', str(private_key), '-pubout', '-out', str(public_key)],
                       check=True, capture_output=True)

    device = attach(args.image)
    try:
        raw = pathlib.Path('/dev/r' + pathlib.Path(device).name)
        require(raw.exists(), f'{raw} is not present')
        sys.path.insert(0, str(args.manifest_tool.parent))
        import manifest as manifest_tool
        print(f'policy: reading {raw} ({OS_BYTES} bytes)')
        with raw.open('rb', buffering=1 << 20) as stream:
            record = manifest_tool.make(stream, OS_BYTES, private_key, public_key,
                                        uuid.UUID(OS_UUID), 1, args.output)
        require(record['image_sha256'] == 'e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2',
                'the OS member is not the pinned original')
        require(record['block_count'] == OS_BLOCKS, f"unexpected block count {record['block_count']}")

        # Re-read and check every block against the table that was just signed.
        table = (args.output / 'hashes.bin').read_bytes()
        samples, lengths = bytearray(), []
        digest = hashlib.sha256()
        indices = [0, 1, OS_BLOCKS // 2, OS_BLOCKS - 1]
        with raw.open('rb', buffering=1 << 20) as stream:
            for index in range(OS_BLOCKS):
                want = min(BLOCK, OS_BYTES - index * BLOCK)
                block = stream.read(want)
                require(len(block) == want, f'short read at block {index}')
                require(hashlib.sha256(block).digest() == table[index * 32:(index + 1) * 32],
                        f'block {index} does not match the signed table')
                digest.update(block)
                if index in indices:
                    samples += block
                    lengths.append(len(block))
            require(not stream.read(1), 'the device is larger than the declared image')
        require(digest.hexdigest() == record['image_sha256'], 'image digest differs from the manifest')
    finally:
        detach(device)

    header = (args.output / 'header.bin').read_bytes()
    signature = (args.output / 'signature.bin').read_bytes()
    key = (args.output / 'trusted-key.bin').read_bytes()
    require(rsa_verify(key, hashlib.sha256(header).digest(), signature), 'the manifest signature does not verify')
    (args.output / 'original-samples.bin').write_bytes(bytes(samples))
    verification = dict(
        image_bytes=OS_BYTES,
        blocks_verified=OS_BLOCKS,
        image_sha256=record['image_sha256'],
        volume_uuid=OS_UUID,
        generation=1,
        trusted_key_sha256=record['trusted_key_sha256'],
        actual_sample_indices=indices,
        actual_sample_lengths=lengths,
        # Real RSA-3072 verification over the manifest header plus a per-block
        # comparison against the signed hash table, both from the attached device.
        real_c_signature_and_block_verification=True,
        verification_method='RSA-3072 PKCS1v1.5 over the header and every block compared against the signed table',
        original_image_modified=False,
        hardware_tested=False,
        boot_root_authenticated=False,
    )
    (args.output / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    print(f"policy: {OS_BLOCKS} blocks verified, image sha256 {record['image_sha256']}")


if __name__ == '__main__':
    main()
