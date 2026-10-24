# UEFI overlay（Mu-Silicium 侧改动）

> **注意：本目录内容由 AI 生成和整理，可能与实际设备行为存在偏差。**
> 这里的代码只对本次使用的 Mu-Silicium 版本、caihong 固件与 XNU 内核验证过；
> 用到别的设备或别的版本前请自行核对。刷机有变砖风险。

把本目录下的文件按**相同路径**覆盖到 Mu-Silicium 源码树（本机是 `D:\uefi`）即可。
所有路径都相对于 Mu-Silicium 根目录。

## 文件说明

| 路径 | 作用 |
|---|---|
| `Platforms/OnePlus/caihongPkg/Drivers/CaihongInitrdDxe/CaihongInitrdDxe.c` | 把 payload 所在的 UFS 分区（`userdata`）暴露成只读块设备，UniXNU 才能读到 `\EFI\UniXNU\unixnu.toml`。含早期的屏幕诊断输出（`Mark()` / `ShowValue()`） |
| `Platforms/OnePlus/caihongPkg/Drivers/CaihongInitrdDxe/CaihongInitrdDxe.inf` | 上面那个驱动的 INF |
| `Platforms/OnePlus/caihongPkg/caihong.fdf` | 把 `CaihongInitrdDxe`、`UniXNU`、`UniXNUDxe`、`DarwinBoot` 四个 INF 打进固件卷 |
| `build_uefi.py` | 构建入口的一处小改动 |
| `Resources/DTBs/caihong.dtb` | 设备树，ADT 生成器的 GIC / 定时器 / DRAM 基址来源 |
| `UniXNU/package/UniXNUPkg/Library/UniXNULib/Main.c` | 加了「启动失败就停住」：`UniXNUBootEntry` 返回错误后 `CpuDeadLoop()`，让 DarwinBoot 的报错留在屏幕上而不是被菜单重画覆盖 |

## 还需要手工做的（不在本目录）

1. `Platforms/OnePlus/caihongPkg/DeviceBuild.py` 的 `PackagesPath` 加
   `"UniXNU/package"` 和 `"UniXNU/drivers/DarwinBoot"`（必须在前两行）
2. `caihong.dsc` 的 `[LibraryClasses]` 加 `UniXNULib`、`FdtLib`
3. `caihong.dsc` 的 `[Components]` 加上面那四个 INF
4. `Silicon/Silicium/SiliciumPkg/Sec/AArch64/ModuleEntryPoint.S`：
   在平台代码用掉 `x0` 之前把 ABL 传来的 FDT 指针存进 `gSecEntryArg0`
5. `Sec/Sec.c` 把 `gSecEntryArg0` 发布成 HOB；`Sec/Sec.inf` 与
   `SiliciumPkg.dec` 补 `gSiliciumEntryArgHobGuid`

细节见仓库根目录 README 的「固件集成」一节。

## 编译

```bash
cd D:/uefi
export PATH="/c/Program Files/LLVM/bin:$PATH" PYTHONUTF8=1
python build_uefi.py -d caihong -m 0 -r RELEASE
# 产物：D:/uefi/Mu-caihong-0.img
```

两个坑：

- `clang` 必须在 PATH（`C:\Program Files\LLVM\bin`），否则 BootShim 编不过
- 中文 Windows 下 `edk2toollib/utility_functions.py` 会因为 GBK 输出解码崩溃，
  把 `decode(..., errors=encodingErrors)` 改成 `errors="replace"`
