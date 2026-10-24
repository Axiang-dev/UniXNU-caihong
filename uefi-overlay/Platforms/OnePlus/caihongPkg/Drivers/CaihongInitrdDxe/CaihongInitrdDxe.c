/** @file
  Expose the UniXNU payload as a read-only block device.

  The payload is a FAT16 volume labelled CAIHONGESP. The kernel is large, so the
  volume normally travels in the ramdisk field of the boot image; a small
  payload can also be embedded in the firmware volume for bring-up. The volume
  is located in this order:

  1. /chosen/linux,initrd-start and linux,initrd-end of the device tree the
     bootloader passed in x0 (Sec keeps it in gSiliciumEntryArgHobGuid).
  2. A signature scan of the large backed memory regions.
  3. The FREEFORM file in the firmware volume (caihong.fdf).

  Every address is validated against the memory map before it is dereferenced:
  an earlier version read whatever it found and took the tablet down with a
  data abort on a range that was mapped but not backed.

  Block I/O callbacks adapted from UniXNU uefi-sheng ShengBootMemoryDxe and the
  giulia port. SPDX-License-Identifier: BSD-2-Clause-Patent
**/
#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/FdtLib.h>
#include <Library/HobLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Protocol/BlockIo.h>
#include <Protocol/DevicePath.h>
#include <Protocol/FirmwareVolume2.h>
#include <Protocol/GraphicsOutput.h>

// ScanMemory and FindEmbedded are kept for the next iteration but not called
// while the device tree path is being diagnosed.
#pragma clang diagnostic ignored "-Wunused-function"

#define RAM_BLOCK_SIZE   512
#define FAT_LABEL_OFF    43
#define FAT_LABEL_LEN    11
#define FAT_TYPE_OFF     54
#define FAT_SECTORS_OFF  32
#define FDT_TOTALSIZE    4
// Qualcomm trees are large: this device reports 17 MiB, so the limit is set
// well above that and FdtCheckHeader decides whether the blob is sane.
#define FDT_MAX_SIZE     (32 * 1024 * 1024)
#define FDT_MAGIC        0xD00DFEED
#define SCAN_MIN_REGION  (16ULL * 1024 * 1024)

//
// Must match FILE FREEFORM in caihong.fdf.
//
STATIC CONST EFI_GUID  mPayloadGuid = {
  0x6E2A1C55, 0x4B7D, 0x4E1A, { 0x9C, 0x3F, 0x2D, 0x5B, 0x8A, 0x7E, 0x4C, 0x91 }
};

STATIC CONST CHAR8  mLabel[FAT_LABEL_LEN] = { 'C', 'A', 'I', 'H', 'O', 'N', 'G', 'E', 'S', 'P', ' ' };
STATIC CONST CHAR8  mFat16[8]             = { 'F', 'A', 'T', '1', '6', ' ', ' ', ' ' };

#pragma pack(1)
typedef struct {
  VENDOR_DEVICE_PATH          Vendor;
  EFI_DEVICE_PATH_PROTOCOL    End;
} RAM_BLOCK_DEVICE_PATH;
#pragma pack()

STATIC RAM_BLOCK_DEVICE_PATH  mDevicePath = {
  {
    { HARDWARE_DEVICE_PATH, HW_VENDOR_DP, { (UINT8)sizeof (VENDOR_DEVICE_PATH), (UINT8)(sizeof (VENDOR_DEVICE_PATH) >> 8) } },
    { 0x6D1A7C42, 0x3E55, 0x4B90, { 0x9A, 0x1D, 0x2C, 0x77, 0x51, 0x08, 0xB4, 0xE3 } }
  },
  { END_DEVICE_PATH_TYPE, END_ENTIRE_DEVICE_PATH_SUBTYPE, { sizeof (EFI_DEVICE_PATH_PROTOCOL), 0 } }
};

STATIC UINT8                 *mRamBase;
STATIC EFI_BLOCK_IO_MEDIA     mMedia;

STATIC
EFI_STATUS
EFIAPI
RamBlockReset (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN BOOLEAN                ExtendedVerification
  )
{
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
RamBlockRead (
  IN  EFI_BLOCK_IO_PROTOCOL  *This,
  IN  UINT32                 MediaId,
  IN  EFI_LBA                Lba,
  IN  UINTN                  BufferSize,
  OUT VOID                   *Buffer
  )
{
  if (Buffer == NULL) {
    return EFI_INVALID_PARAMETER;
  }

  if (MediaId != mMedia.MediaId) {
    return EFI_MEDIA_CHANGED;
  }

  if ((BufferSize % RAM_BLOCK_SIZE) != 0) {
    return EFI_BAD_BUFFER_SIZE;
  }

  if ((Lba > mMedia.LastBlock) || ((BufferSize / RAM_BLOCK_SIZE) > (mMedia.LastBlock + 1 - Lba))) {
    return EFI_INVALID_PARAMETER;
  }

  CopyMem (Buffer, mRamBase + Lba * RAM_BLOCK_SIZE, BufferSize);
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
RamBlockWrite (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN UINT32                 MediaId,
  IN EFI_LBA                Lba,
  IN UINTN                  BufferSize,
  IN VOID                   *Buffer
  )
{
  return EFI_WRITE_PROTECTED;
}

STATIC
EFI_STATUS
EFIAPI
RamBlockFlush (
  IN EFI_BLOCK_IO_PROTOCOL  *This
  )
{
  return EFI_SUCCESS;
}

STATIC EFI_BLOCK_IO_PROTOCOL  mBlockIo = {
  EFI_BLOCK_IO_PROTOCOL_REVISION,
  &mMedia,
  RamBlockReset,
  RamBlockRead,
  RamBlockWrite,
  RamBlockFlush
};

STATIC
UINT64
ReadAddress (
  IN CONST VOID  *Property,
  IN INT32       Length
  )
{
  // Device tree properties are big-endian.
  if (Length == 8) {
    return SwapBytes64 (ReadUnaligned64 ((CONST UINT64 *)Property));
  }

  return SwapBytes32 (ReadUnaligned32 ((CONST UINT32 *)Property));
}

/**
  The device tree magic is stored big-endian. Comparing a little-endian word
  against 0xD00DFEED rejects every valid tree, which is exactly what an earlier
  version did.
**/
STATIC
BOOLEAN
IsFdt (
  IN CONST UINT8  *Blob
  )
{
  return (Blob[0] == 0xD0) && (Blob[1] == 0x0D) && (Blob[2] == 0xFE) && (Blob[3] == 0xED);
}

/**
  Paint a bar across the top of the screen. The text console is not connected
  while drivers are being dispatched, so this is the only way to see how far
  this driver got: blue means it started, green that the device tree range
  worked, yellow that the scan found the volume, red that nothing was found.
**/
STATIC
VOID
Mark (
  IN UINT8  Red,
  IN UINT8  Green,
  IN UINT8  Blue
  )
{
  EFI_GRAPHICS_OUTPUT_PROTOCOL   *Gop;
  EFI_GRAPHICS_OUTPUT_BLT_PIXEL  Pixel;

  if (EFI_ERROR (gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop))) {
    return;
  }

  Pixel.Red      = Red;
  Pixel.Green    = Green;
  Pixel.Blue     = Blue;
  Pixel.Reserved = 0;
  Gop->Blt (Gop, &Pixel, EfiBltVideoFill, 0, 0, 0, 0, 600, 100, 0);

  // Hold the colour long enough to be read from a photo of the panel.
  gBS->Stall (2500000);
}

//
// 8x8 glyphs for the hex digits, enough to put a raw pointer on the panel.
//
STATIC CONST UINT8  mHex[16][8] = {
  { 0x3C, 0x66, 0x6E, 0x76, 0x66, 0x66, 0x3C, 0x00 }, // 0
  { 0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7E, 0x00 }, // 1
  { 0x3C, 0x66, 0x06, 0x0C, 0x30, 0x60, 0x7E, 0x00 }, // 2
  { 0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00 }, // 3
  { 0x0C, 0x1C, 0x3C, 0x6C, 0x7E, 0x0C, 0x0C, 0x00 }, // 4
  { 0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00 }, // 5
  { 0x1C, 0x30, 0x60, 0x7C, 0x66, 0x66, 0x3C, 0x00 }, // 6
  { 0x7E, 0x66, 0x06, 0x0C, 0x18, 0x18, 0x18, 0x00 }, // 7
  { 0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00 }, // 8
  { 0x3C, 0x66, 0x66, 0x3E, 0x06, 0x0C, 0x38, 0x00 }, // 9
  { 0x18, 0x3C, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x00 }, // A
  { 0x7C, 0x66, 0x66, 0x7C, 0x66, 0x66, 0x7C, 0x00 }, // B
  { 0x3C, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3C, 0x00 }, // C
  { 0x78, 0x6C, 0x66, 0x66, 0x66, 0x6C, 0x78, 0x00 }, // D
  { 0x7E, 0x60, 0x60, 0x7C, 0x60, 0x60, 0x7E, 0x00 }, // E
  { 0x7E, 0x60, 0x60, 0x7C, 0x60, 0x60, 0x60, 0x00 }, // F
};

STATIC
VOID
DrawNibble (
  IN EFI_GRAPHICS_OUTPUT_PROTOCOL  *Gop,
  IN UINTN                         X,
  IN UINTN                         Y,
  IN UINTN                         Scale,
  IN UINT8                         Digit
  )
{
  EFI_GRAPHICS_OUTPUT_BLT_PIXEL  White = { 255, 255, 255, 0 };
  EFI_GRAPHICS_OUTPUT_BLT_PIXEL  Black = { 0, 0, 0, 0 };
  UINT8                          Row;
  UINT8                          Column;

  for (Row = 0; Row < 8; Row++) {
    for (Column = 0; Column < 8; Column++) {
      BOOLEAN  On = (mHex[Digit & 0xF][Row] & (0x80 >> Column)) != 0;

      Gop->Blt (
             Gop,
             On ? &White : &Black,
             EfiBltVideoFill,
             0, 0,
             X + Column * Scale,
             Y + Row * Scale,
             Scale, Scale,
             0
             );
    }
  }
}

/**
  Put a 64-bit value on the panel as 16 hex digits, top left of the screen.
**/
STATIC
VOID
ShowValue (
  IN UINT64  Value,
  IN UINTN   Y
  )
{  EFI_GRAPHICS_OUTPUT_PROTOCOL   *Gop;
  EFI_GRAPHICS_OUTPUT_BLT_PIXEL  Black = { 0, 0, 0, 0 };
  UINTN                          Index;
  UINTN                          Scale = 4;
  UINTN                          Advance = 8 * Scale + 4;

  if (EFI_ERROR (gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop))) {
    return;
  }

  // Clear the strip first so stale digits do not confuse the reader.
  Gop->Blt (Gop, &Black, EfiBltVideoFill, 0, 0, 0, Y, 16 * Advance, 8 * Scale, 0);

  for (Index = 0; Index < 16; Index++) {
    DrawNibble (Gop, Index * Advance, Y, Scale, (UINT8)((Value >> (60 - Index * 4)) & 0xF));
  }
}

STATIC
BOOLEAN
RangeIsBacked (
  IN EFI_MEMORY_DESCRIPTOR  *Map,
  IN UINTN                  Size,
  IN UINTN                  DescriptorSize,
  IN UINT64                 Start,
  IN UINT64                 Length
  )
{
  UINT64  End;
  UINT64  Covered;
  UINTN   At;

  if (Length == 0) {
    return FALSE;
  }

  End     = Start + Length;
  Covered = Start;
  if (End < Start) {
    return FALSE;
  }

  while (Covered < End) {
    BOOLEAN  Advanced = FALSE;

    for (At = 0; At + DescriptorSize <= Size; At += DescriptorSize) {
      EFI_MEMORY_DESCRIPTOR  *Entry      = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)Map + At);
      UINT64                 EntryStart = Entry->PhysicalStart;
      UINT64                 EntryEnd   = EntryStart + EFI_PAGES_TO_SIZE (Entry->NumberOfPages);

      if ((Covered < EntryStart) || (Covered >= EntryEnd)) {
        continue;
      }

      if ((Entry->Attribute & EFI_MEMORY_RP) != 0) {
        return FALSE;
      }

      if ((Entry->Type == EfiMemoryMappedIO) || (Entry->Type == EfiMemoryMappedIOPortSpace)) {
        return FALSE;
      }

      Covered  = EntryEnd;
      Advanced = TRUE;
      break;
    }

    if (!Advanced) {
      return FALSE;
    }
  }

  return TRUE;
}

STATIC
EFI_STATUS
InstallVolume (
  IN UINT64  At,
  IN UINT64  Bytes
  )
{
  EFI_STATUS  Status;
  EFI_HANDLE  Handle;

  mRamBase             = (UINT8 *)(UINTN)At;
  mMedia.MediaId       = 1;
  mMedia.MediaPresent  = TRUE;
  mMedia.ReadOnly      = TRUE;
  mMedia.BlockSize     = RAM_BLOCK_SIZE;
  mMedia.IoAlign       = 1;
  mMedia.LastBlock     = Bytes / RAM_BLOCK_SIZE - 1;

  Handle = NULL;
  Status = gBS->InstallMultipleProtocolInterfaces (
                  &Handle,
                  &gEfiDevicePathProtocolGuid,
                  &mDevicePath,
                  &gEfiBlockIoProtocolGuid,
                  &mBlockIo,
                  NULL
                  );
  DEBUG ((DEBUG_ERROR, "CAIHONGINITRD: FAT16 at 0x%lx size 0x%lx: %r\n", At, Bytes, Status));
  return Status;
}

STATIC
UINT64
VolumeSizeAt (
  IN UINT8   *Sector,
  IN UINT64  Limit
  )
{
  UINT64  Bytes;

  if (Limit < RAM_BLOCK_SIZE) {
    return 0;
  }

  if ((Sector[510] != 0x55) || (Sector[511] != 0xAA)) {
    return 0;
  }

  if (ReadUnaligned16 ((CONST UINT16 *)(Sector + 11)) != RAM_BLOCK_SIZE) {
    return 0;
  }

  if (CompareMem (Sector + FAT_LABEL_OFF, mLabel, FAT_LABEL_LEN) != 0) {
    return 0;
  }

  if (CompareMem (Sector + FAT_TYPE_OFF, mFat16, sizeof (mFat16)) != 0) {
    return 0;
  }

  Bytes = (UINT64)ReadUnaligned32 ((CONST UINT32 *)(Sector + FAT_SECTORS_OFF)) * RAM_BLOCK_SIZE;
  if ((Bytes == 0) || (Bytes > Limit)) {
    return 0;
  }

  return Bytes;
}

STATIC
EFI_STATUS
FindVolume (
  IN UINT64  Start,
  IN UINT64  End
  )
{
  UINT64  At;
  UINT64  Bytes;

  if (End <= Start) {
    return EFI_NOT_FOUND;
  }

  for (At = ALIGN_VALUE (Start, RAM_BLOCK_SIZE); At + RAM_BLOCK_SIZE <= End; At += RAM_BLOCK_SIZE) {
    Bytes = VolumeSizeAt ((UINT8 *)(UINTN)At, End - At);
    if (Bytes != 0) {
      return InstallVolume (At, Bytes);
    }
  }

  return EFI_NOT_FOUND;
}

/**
  Look for the volume in the backed memory regions that are large enough to hold
  it. The bootloader aligns the ramdisk to at least 2 MiB, so the coarse passes
  almost always hit; the finer passes only run when they do not. Scanning every
  512-byte offset directly takes long enough to look like a hang.
**/
STATIC
EFI_STATUS
ScanMemory (
  IN EFI_MEMORY_DESCRIPTOR  *Map,
  IN UINTN                  Size,
  IN UINTN                  DescriptorSize
  )
{
  STATIC CONST UINT64  Steps[] = { 2 * 1024 * 1024, 64 * 1024, 4 * 1024 };
  UINTN                Step;
  UINTN                At;
  UINT64               Start;
  UINT64               End;
  UINT64               Address;
  UINT64               Bytes;

  for (Step = 0; Step < ARRAY_SIZE (Steps); Step++) {
    for (At = 0; At + DescriptorSize <= Size; At += DescriptorSize) {
      EFI_MEMORY_DESCRIPTOR  *Entry = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)Map + At);

      if ((Entry->Attribute & EFI_MEMORY_RP) != 0) {
        continue;
      }

      if ((Entry->Type != EfiConventionalMemory) &&
          (Entry->Type != EfiBootServicesData) &&
          (Entry->Type != EfiLoaderData) &&
          (Entry->Type != EfiACPIReclaimMemory))
      {
        continue;
      }

      Start = Entry->PhysicalStart;
      End   = Start + EFI_PAGES_TO_SIZE (Entry->NumberOfPages);
      if ((End - Start) < SCAN_MIN_REGION) {
        continue;
      }

      for (Address = ALIGN_VALUE (Start, Steps[Step]); Address + RAM_BLOCK_SIZE <= End; Address += Steps[Step]) {
        Bytes = VolumeSizeAt ((UINT8 *)(UINTN)Address, End - Address);
        if (Bytes != 0) {
          return InstallVolume (Address, Bytes);
        }
      }
    }
  }

  return EFI_NOT_FOUND;
}

/**
  Fall back to the payload embedded in the firmware volume.
**/
STATIC
EFI_STATUS
FindEmbedded (
  VOID
  )
{
  EFI_FIRMWARE_VOLUME2_PROTOCOL  *Fv;
  EFI_HANDLE                     *Handles;
  UINTN                          Count;
  UINTN                          Index;
  VOID                           *Buffer;
  UINTN                          BufferSize;
  UINT32                         AuthenticationStatus;
  UINT64                         Bytes;
  EFI_STATUS                     Status;

  Status = gBS->LocateHandleBuffer (
                  ByProtocol,
                  &gEfiFirmwareVolume2ProtocolGuid,
                  NULL,
                  &Count,
                  &Handles
                  );
  if (EFI_ERROR (Status)) {
    return Status;
  }

  for (Index = 0; Index < Count; Index++) {
    Status = gBS->HandleProtocol (Handles[Index], &gEfiFirmwareVolume2ProtocolGuid, (VOID **)&Fv);
    if (EFI_ERROR (Status)) {
      continue;
    }

    Buffer     = NULL;
    BufferSize = 0;
    Status     = Fv->ReadSection (
                       Fv,
                       &mPayloadGuid,
                       EFI_SECTION_RAW,
                       0,
                       &Buffer,
                       &BufferSize,
                       &AuthenticationStatus
                       );
    if (EFI_ERROR (Status) || (Buffer == NULL)) {
      continue;
    }

    Bytes = VolumeSizeAt ((UINT8 *)Buffer, BufferSize);
    if (Bytes == 0) {
      FreePool (Buffer);
      continue;
    }

    Status = InstallVolume ((UINT64)(UINTN)Buffer, Bytes);
    FreePool (Handles);
    return Status;
  }

  FreePool (Handles);
  return EFI_NOT_FOUND;
}

EFI_STATUS
EFIAPI
CaihongInitrdDxeEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_HOB_GUID_TYPE      *Hob;
  EFI_MEMORY_DESCRIPTOR  *Map;
  UINTN                  Size = 0;
  UINTN                  Key;
  UINTN                  DescriptorSize;
  UINT32                 Version;
  CONST VOID             *Fdt;
  CONST VOID             *StartProp;
  CONST VOID             *EndProp;
  INT32                  Chosen;
  INT32                  StartLength;
  INT32                  EndLength;
  UINT32                 TotalSize;
  UINT64                 Start;
  UINT64                 End;
  UINT64                 Dtb;
  EFI_STATUS             Status;

  Status = gBS->GetMemoryMap (&Size, NULL, &Key, &DescriptorSize, &Version);
  if (Status != EFI_BUFFER_TOO_SMALL) {
    return Status;
  }

  Size += 8 * DescriptorSize;
  Map   = AllocatePool (Size);
  if (Map == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }

  Status = gBS->GetMemoryMap (&Size, Map, &Key, &DescriptorSize, &Version);
  if (EFI_ERROR (Status)) {
    FreePool (Map);
    return Status;
  }

  Mark (0, 0, 255);

  //
  // 1. The initrd range from the device tree the bootloader passed in x0.
  //    Each failure paints its own colour so the step is visible on a photo.
  //
  Hob = GetFirstGuidHob (&gSiliciumEntryArgHobGuid);
  if ((Hob == NULL) || (GET_GUID_HOB_DATA_SIZE (Hob) < sizeof (UINT64))) {
    Mark (128, 0, 128);   // purple: no entry argument HOB
  } else {
    Dtb = ReadUnaligned64 ((CONST UINT64 *)GET_GUID_HOB_DATA (Hob));
    ShowValue (Dtb, 140);
    if (Dtb == 0) {
      Mark (255, 0, 255);        // magenta: x0 was zero
    } else if ((Dtb & 7) != 0) {
      Mark (255, 105, 180);      // pink: x0 is not 8-byte aligned
    } else if (!RangeIsBacked (Map, Size, DescriptorSize, Dtb, 64)) {
      Mark (128, 128, 128);      // grey: x0 points outside backed memory
    } else if (!IsFdt ((CONST UINT8 *)(UINTN)Dtb)) {
      Mark (0, 0, 128);          // navy: memory is readable but not an FDT
    } else {
      Fdt       = (CONST VOID *)(UINTN)Dtb;
      TotalSize = SwapBytes32 (ReadUnaligned32 ((CONST UINT32 *)((CONST UINT8 *)Fdt + FDT_TOTALSIZE)));
      ShowValue (TotalSize, 260);
      ShowValue (SwapBytes32 (ReadUnaligned32 ((CONST UINT32 *)((CONST UINT8 *)Fdt + 20))), 380);
      Chosen    = -1;
      if ((TotalSize < 64) || (TotalSize > FDT_MAX_SIZE)) {
        Mark (255, 128, 0);        // orange: implausible totalsize
      } else if (!RangeIsBacked (Map, Size, DescriptorSize, Dtb, TotalSize)) {
        Mark (128, 128, 128);      // grey: the tree runs past backed memory
      } else if ((Chosen = FdtPathOffset (Fdt, "/chosen")) < 0) {
        Mark (0, 255, 255);        // cyan: valid tree without /chosen
      } else {
        StartProp = FdtGetProp (Fdt, Chosen, "linux,initrd-start", &StartLength);
        EndProp   = FdtGetProp (Fdt, Chosen, "linux,initrd-end", &EndLength);
        if ((StartProp == NULL) || (EndProp == NULL) ||
            ((StartLength != 4) && (StartLength != 8)) ||
            ((EndLength != 4) && (EndLength != 8)))
        {
          Mark (255, 255, 0); // yellow: /chosen without an initrd range
        } else {
          Start = ReadAddress (StartProp, StartLength);
          End   = ReadAddress (EndProp, EndLength);
          ShowValue (Start, 500);
          ShowValue (End, 620);
          if (End <= Start) {
            Mark (255, 255, 255); // white: the range is empty or inverted
          } else {
            // The bootloader put the ramdisk here, so the range is real memory
            // even when the firmware memory map does not describe it.
            Mark (0, 255, 0);  // green: looking for the volume
            if (!EFI_ERROR (FindVolume (Start, End))) {
              FreePool (Map);
              return EFI_SUCCESS;
            }
            Mark (255, 0, 0);  // red: no CAIHONGESP volume inside the range
          }
        }
      }
    }
  }

  FreePool (Map);
  gBS->Stall (8000000);
  return EFI_NOT_FOUND;
}
