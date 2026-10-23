#!/usr/bin/env python3
"""Point AMFI's PMGR lookup at this board's stand-in service.

AMFI's ConfigurationSettings looks the service up by name:

    pmgr = IOService::waitForService(IOService::serviceMatching("ApplePMGRNub"));
    if (!pmgr) panic("AMFI: No PMGR?\\n");

ApplePMGRNub only appears when Apple's ApplePMGR driver matches an
AppleARMIODevice provider. ArmDTPlatform publishes IOPlatformDevice instead, and
the PMGR hardware Apple's driver would drive does not exist on SM8650, so the
lookup can never succeed.

This renames the lookup string in place to "CaihongPMGRN", a class the board
provides (drivers/CaihongPmgrNub). Both names are twelve bytes, so the edit
keeps every offset in the prelinked image.

AMFI then calls the nub's read and write entry points, which dispatch through the
provider at nub+0x88 into ApplePMGR's virtual table. The stand-in has no such
table, so both entries are reduced to "read returns 0, write does nothing": AMFI
only reads a value, sets bit 2 and writes it back.

Usage: patch-amfi-pmgr-name.py <kernel.macho>
"""
import pathlib
import struct
import sys

APPLE = b"ApplePMGRNub"
OURS = b"CaihongPMGRN"
# Only the AMFI copy is renamed; Apple's own kext keeps its class name.
CONTEXT = b"AMFI: PMGRAON"

# Located by their exact instruction sequences in __PLK_TEXT_EXEC; the layout is
# identical across kernels prelinked from the same iPadOS 17.7.11 cache.
PMGR_READ_VA = 0xFFFFFFF004A24ADC
PMGR_READ_ORIGINAL = (0xF9404400, 0xB4000080, 0xF9400008, 0xF9450101, 0xD61F0020)
PMGR_WRITE_VA = 0xFFFFFFF004A230EC
PMGR_WRITE_ORIGINAL = (0xF9404400, 0xB4000080, 0xF9400008, 0xF9450502, 0xD61F0040)

MOV_W0_0 = 0x52800000
RET = 0xD65F03C0

# When waitForMatchingService returns null AMFI jumps here and calls panic. Both
# instructions are replaced with "return 0" and a branch to the function's own
# epilogue, so the frame is unwound exactly as on the normal path.
PMGR_CHECK_FAIL_VA = 0xFFFFFFF004AAECFC
PMGR_CHECK_FAIL_ORIGINAL = (0xAA1503E0, 0xAA1503E1)   # mov x0,x21 ; mov x1,x21
PMGR_CHECK_EPILOGUE_VA = 0xFFFFFFF004AAEC4C


def branch(from_va, to_va):
    delta = (to_va - from_va) // 4
    if not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit("branch out of range")
    return 0x14000000 | (delta & 0x3FFFFFF)


def segments(data):
    magic, cpu, sub, kind, ncmds, _ = struct.unpack_from("<IiiIII", data, 0)
    offset = 32
    found = []
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmd == 0x19:
            name = data[offset + 8:offset + 24].rstrip(b"\0").decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, offset + 24)
            found.append((name, vmaddr, vmsize, fileoff, filesize))
        offset += cmdsize
    return found


def file_offset(data, va):
    for name, vmaddr, vmsize, fileoff, filesize in segments(data):
        if vmaddr <= va < vmaddr + filesize:
            return fileoff + (va - vmaddr)
    raise SystemExit(f"0x{va:x} is not backed by any segment")


def patch_entry(data, va, expected, replacement):
    at = file_offset(data, va)
    actual = struct.unpack_from("<%dI" % len(expected), data, at)
    if actual[:len(replacement)] == replacement:
        return False
    if actual != expected:
        raise SystemExit(f"unexpected instructions at 0x{va:x}: {[hex(x) for x in actual]}")
    for index, word in enumerate(replacement):
        struct.pack_into("<I", data, at + index * 4, word)
    return True


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = pathlib.Path(sys.argv[1])
    data = bytearray(path.read_bytes())
    if len(APPLE) != len(OURS):
        raise SystemExit("the two service names must be the same length")

    hits = []
    start = 0
    while True:
        index = data.find(APPLE, start)
        if index < 0:
            break
        window = bytes(data[index:index + 400])
        if CONTEXT in window:
            hits.append(index)
        start = index + 1

    if not hits:
        # A kernel that already carries the rename (a re-run on the same file).
        if OURS in bytes(data) and CONTEXT in bytes(data):
            print("AMFI PMGR lookup already renamed")
            patch_entry(data, PMGR_READ_VA, PMGR_READ_ORIGINAL, (MOV_W0_0, RET))
            patch_entry(data, PMGR_WRITE_VA, PMGR_WRITE_ORIGINAL, (RET,))
            patch_entry(data, PMGR_CHECK_FAIL_VA, PMGR_CHECK_FAIL_ORIGINAL,
                        (MOV_W0_0, branch(PMGR_CHECK_FAIL_VA + 4, PMGR_CHECK_EPILOGUE_VA)))
            path.write_bytes(data)
            print("PMGR read/write inert, AMFI check returns 0")
            return
        raise SystemExit("no AMFI PMGR lookup string found")
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one AMFI lookup string, found {len(hits)}")

    offset = hits[0]
    data[offset:offset + len(APPLE)] = OURS
    print(f"AMFI PMGR lookup renamed at 0x{offset:x}: {APPLE.decode()} -> {OURS.decode()}")

    patch_entry(data, PMGR_READ_VA, PMGR_READ_ORIGINAL, (MOV_W0_0, RET))
    print(f"PMGR read at 0x{PMGR_READ_VA:x} now returns 0")
    patch_entry(data, PMGR_WRITE_VA, PMGR_WRITE_ORIGINAL, (RET,))
    print(f"PMGR write at 0x{PMGR_WRITE_VA:x} now does nothing")

    patch_entry(data, PMGR_CHECK_FAIL_VA, PMGR_CHECK_FAIL_ORIGINAL,
                (MOV_W0_0, branch(PMGR_CHECK_FAIL_VA + 4, PMGR_CHECK_EPILOGUE_VA)))
    print(f"AMFI PMGR check at 0x{PMGR_CHECK_FAIL_VA:x} now returns 0 instead of panicking")

    path.write_bytes(data)


if __name__ == "__main__":
    main()
