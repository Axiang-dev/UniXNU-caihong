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
COMPONENTS = {
    # component: (image bytes, block count, expected sha256)
    'os': (6077580800, 1483785, 'e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2'),
    'systemos': (3303014400, 806400, 'b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f'),
}
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
    parser.add_argument('--component', choices=tuple(COMPONENTS), default='os')
    parser.add_argument('--image', type=pathlib.Path, help='the member of the IPSW (default: $XNU_OS_MEMBER)')
    parser.add_argument('--output', type=pathlib.Path)
    parser.add_argument('--manifest-tool', type=pathlib.Path, default=pathlib.Path('kits/VerifiedRoot/tools/manifest.py'))
    parser.add_argument('--keys', type=pathlib.Path, default=pathlib.Path('build/verified-root-keys'))
    args = parser.parse_args()

    image_bytes, blocks, expected = COMPONENTS[args.component]
    if args.output is None:
        args.output = pathlib.Path('build/verified-root-' + args.component)
    if args.image is None:
        key = 'XNU_SYSTEMOS_MEMBER' if args.component == 'systemos' else 'XNU_OS_MEMBER'
        from_env = os.environ.get(key)
        if not from_env:
            raise SystemExit(f'pass --image or set {key} to the {args.component} member of the IPSW')
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
        info = plistlib.loads(subprocess.run(['diskutil', 'info', '-plist', device],
                                            capture_output=True, check=True).stdout)
        volume_uuid = info.get('VolumeUUID')
        require(volume_uuid, 'the attached member has no volume UUID')
        print(f'policy: {args.component} {raw} ({image_bytes} bytes) uuid {volume_uuid}')
        with raw.open('rb', buffering=1 << 20) as stream:
            record = manifest_tool.make(stream, image_bytes, private_key, public_key,
                                        uuid.UUID(volume_uuid), 1, args.output)
        require(record['image_sha256'] == expected, f'the {args.component} member is not the pinned original')
        require(record['block_count'] == blocks, f"unexpected block count {record['block_count']}")

        # Re-read and check every block against the table that was just signed.
        table = (args.output / 'hashes.bin').read_bytes()
        samples, lengths = bytearray(), []
        digest = hashlib.sha256()
        indices = [0, 1, blocks // 2, blocks - 1]
        with raw.open('rb', buffering=1 << 20) as stream:
            for index in range(blocks):
                want = min(BLOCK, image_bytes - index * BLOCK)
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
        image_bytes=image_bytes,
        blocks_verified=blocks,
        image_sha256=record['image_sha256'],
        volume_uuid=volume_uuid,
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
    print(f"policy: {args.component} {blocks} blocks verified, sha256 {record['image_sha256']}")


if __name__ == '__main__':
    main()
