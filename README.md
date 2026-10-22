# UniXNU-caihong

OnePlus Pad Pro（`caihong` / OPD2404 / SM8650）的 UniXNU 设备仓库。

当前状态：**骨架已建，未做固件集成**。已通过官方 `unixnu` 驱动校验
（`unixnu status` 能识别板子，`unixnu defconfig` 能落 `.config`）。

## 已完成

| 文件 | 说明 |
|---|---|
| `board.toml` | 板级描述。值来自 `D:\uefi\Resources\Configs\caihong.toml`、`caihong.dtb` 和 giulia 参考移植 |
| `configs/caihong_defconfig` | 第一阶段内核配置（只求 kernel entry，不开存储/显示/GPU 驱动） |
| `scripts/make-caihong-adt.py` | ADT 生成器。GIC / 定时器 / DRAM 基址取自 caihong 自己的 DTB |

`board.toml` 里唯一没填的是 `[userdata] first_lba/last_lba`——必须从真机 GPT 读，
不能猜（填错会覆盖别的分区）。

## 还没做：把 UniXNU 接进 UEFI 树

参考实现（`tycykp/UniXNU-OnePlus-Ace5`）的 `patches/giulia-unixnu.patch` 给出了完整配方，
共 8 处改动，全部要在 `D:\uefi` 上做（**建议开分支**）：

1. **`Platforms/OnePlus/caihongPkg/DeviceBuild.py`**
   `PackagesPath` 里加 `"UniXNU/package"` 和 `"UniXNU/drivers/DarwinBoot"`（必须在前两行）。

2. **`caihong.dsc` → `[LibraryClasses]`**
   ```ini
   UniXNULib|UniXNUPkg/Library/UniXNULib/UniXNULib.inf
   FdtLib|MdePkg/Library/BaseFdtLib/BaseFdtLib.inf
   ```

3. **`caihong.dsc` → `[Components]`**
   ```ini
   caihongPkg/Drivers/CaihongInitrdDxe/CaihongInitrdDxe.inf
   UniXNUPkg/Application/UniXNU/UniXNU.inf
   UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf
   DarwinBootPkg/Driver/DarwinBoot.inf
   ```

4. **`caihong.fdf`** 对应位置加同样 4 个 `INF`。

5. **`Silicon/Silicium/SiliciumPkg/Sec/AArch64/ModuleEntryPoint.S`**
   ⚠️ 最关键的一处：ABL 通过 `x0` 传进来的 **FDT 指针必须在平台代码用掉 x0 之前存下来**，
   否则 DarwinBoot 拿不到真机设备树。
   ```asm
   ASM_FUNC (_ModuleEntryPoint)
     adr   x1, ASM_PFX (gSecEntryArg0)
     str   x0, [x1]
     dc    cvac, x1
     dsb   sy
     ...
     .align 3
     .global ASM_PFX (gSecEntryArg0)
   ASM_PFX (gSecEntryArg0):
     .quad 0
   ```

6. **`Sec/Sec.c`**：把 `gSecEntryArg0` 发布成 HOB
   `BuildGuidDataHob (&gSiliciumEntryArgHobGuid, &gSecEntryArg0, sizeof (gSecEntryArg0));`

7. **`Sec/Sec.inf`** 的 `[Guids]` 加 `gSiliciumEntryArgHobGuid`。

8. **`SiliciumPkg.dec`** 的 `[Guids]` 定义
   `gSiliciumEntryArgHobGuid = { 0xDAEE82E6, 0x8090, 0x4E72, { 0xBF, 0xB9, 0x78, 0xC1, 0x3C, 0xD4, 0xE3, 0x59 } }`

### 还缺一个设备侧驱动：CaihongInitrdDxe

作用：把 boot.img 的 ramdisk（UniXNU payload FAT 卷）暴露成只读块设备，
这样 UniXNU 才能读到 `\EFI\UniXNU\unixnu.toml`。

原型：`D:\unixnu\UniXNU-OnePlus-Ace5\uefi-overlay\Platforms\OnePlus\giuliaPkg\Drivers\GiuliaInitrdDxe\`
- 核心只有 `GiuliaInitrdDxe.c`（346 行）+ `.inf`（38 行）+ `ScanPolicy.h`
- 同目录还有一堆 WiFi/触摸相关的头文件（`WifiPcie*.h`、`Touch*.c`），
  那些是 giulia 的额外诊断，**caihong 先不要**，只搬核心三个文件改名即可。

## 需要的素材

- **UniXNU 源码**：`D:\unixnu\UniXNU`（已下载，4.5 MB）
- **DarwinBoot**：`D:\unixnu\DarwinBoot`（已下载，108 KB；源码包里的 `drivers/DarwinBoot` 是空目录，必须单独拉）
- **iOS IPSW**：`iPad7,1 / 17.7.11 / 21H461`（约 7 GB，Apple CDN 公开可下）
- **macOS**：只有 kernel prelink 和 userdata 需要（Xcode + hdiutil）；
  UEFI + payload 的 EDK2 部分 Windows 就能编

## 建议顺序

1. 在 `D:\uefi` 开分支，做上面 8 处改动 + 移植 CaihongInitrdDxe → 编出带 UniXNU 的 FD
2. 打一个 192 MB 的 payload FAT 卷（`unixnu.toml` + 菜单项），塞进 boot.img 的 ramdisk
3. `fastboot boot` 看 **UniXNU 菜单**（这一步完全不碰 XNU，风险最低）
4. 菜单稳了再上 macOS runner 编 `kernel.macho`
