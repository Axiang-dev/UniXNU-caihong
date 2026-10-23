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

Usage: patch-amfi-pmgr-name.py <kernel.macho>
"""
import pathlib
import sys

APPLE = b"ApplePMGRNub"
OURS = b"CaihongPMGRN"
# Only the AMFI copy is renamed; Apple's own kext keeps its class name.
CONTEXT = b"AMFI: PMGRAON"


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

    if len(hits) != 1:
        raise SystemExit(f"expected exactly one AMFI lookup string, found {len(hits)}")

    offset = hits[0]
    data[offset:offset + len(APPLE)] = OURS
    path.write_bytes(data)
    print(f"AMFI PMGR lookup renamed at 0x{offset:x}: {APPLE.decode()} -> {OURS.decode()}")


if __name__ == "__main__":
    main()
