# UEFI overlay（Mu-Silicium 侧改动）

> **注意：本目录内容由 AI 生成和整理，可能与实际设备行为存在偏差。**
> 这里的代码只对本次使用的 Mu-Silicium 版本、caihong 固件与 XNU 内核验证过；
> 用到别的设备或别的版本前请自行核对。刷机有变砖风险。

## 还原步骤（已实测）

按顺序做三步，可以从上游 Mu-Silicium 还原出能编出可用固件的树：

```bash
git clone https://github.com/Project-Silicium/Mu-Silicium.git uefi
cd uefi
git checkout <基线 commit>            # 本移植用的是 origin/main

# 1. 集成补丁：caihong 平台包、Sec 的 FDT 保存、HOB 发布
git apply /path/to/patches/uefi-integration.patch

# 2. 覆盖本 overlay（驱动、fdf、构建脚本、设备树）
cp -r /path/to/uefi-overlay/Platforms  .
cp -r /path/to/uefi-overlay/Resources  .
cp /path/to/uefi-overlay/build_uefi.py .

# 3. UniXNU 源码放到 UniXNU/（单独 clone，见下）
git clone https://github.com/<UniXNU 仓库> UniXNU
cp /path/to/uefi-overlay/UniXNU/package/UniXNUPkg/Library/UniXNULib/Main.c \
   UniXNU/package/UniXNUPkg/Library/UniXNULib/Main.c
```

第 2 步之后，树里 `caihong.fdf`、`build_uefi.py`、`CaihongInitrdDxe.c`、
`Resources/DTBs/caihong.dtb` 与本移植的实测树**逐字节一致**；
`caihong.dsc`、`DeviceBuild.py` 内容一致（仅换行符随 checkout 设置不同）。

## 文件说明

| 路径 | 作用 |
|---|---|
| `Platforms/OnePlus/caihongPkg/Drivers/CaihongInitrdDxe/CaihongInitrdDxe.c` | 把 payload 所在的 UFS 分区（`userdata`）暴露成只读块设备，UniXNU 才能读到 `\EFI\UniXNU\unixnu.toml`。含早期的屏幕诊断输出（`Mark()` / `ShowValue()`） |
| `.../CaihongInitrdDxe.inf` | 上面那个驱动的 INF |
| `Platforms/OnePlus/caihongPkg/caihong.fdf` | 把 `CaihongInitrdDxe`、`UniXNU`、`UniXNUDxe`、`DarwinBoot` 四个 INF 打进固件卷 |
| `build_uefi.py` | 构建入口的一处小改动 |
| `Resources/DTBs/caihong.dtb` | 设备树，ADT 生成器的 GIC / 定时器 / DRAM 基址来源 |
| `UniXNU/package/UniXNUPkg/Library/UniXNULib/Main.c` | 加了「启动失败就停住」：`UniXNUBootEntry` 返回错误后 `CpuDeadLoop()`，让 DarwinBoot 的报错留在屏幕上而不是被菜单重画覆盖 |

## 集成补丁里有什么

`patches/uefi-integration.patch`（15 个文件）包含：

- `Platforms/OnePlus/caihongPkg/caihong.dsc` —— `UniXNULib`/`FdtLib` 与四个组件
- `Platforms/OnePlus/caihongPkg/DeviceBuild.py` —— `PackagesPath` 加 UniXNU
- `Platforms/OnePlus/caihongPkg/Include/*.inc`、`Library/MemoryMapLib`、
  `Library/ConfigurationMapLib` —— 平台包需要的库
- `Resources/Configs/caihong.toml` —— 平台配置
- `Silicon/Silicium/SiliciumPkg/Sec/AArch64/ModuleEntryPoint.S` ——
  **在平台代码用掉 `x0` 之前把 ABL 传来的 FDT 指针存进 `gSecEntryArg0`**
- `Sec/Sec.c`、`Sec/Sec.inf`、`SiliciumPkg.dec` —— 把 `gSecEntryArg0` 发布成 HOB

## 编译

```bash
cd <Mu-Silicium 的工作目录>
export PATH="/c/Program Files/LLVM/bin:$PATH" PYTHONUTF8=1
python build_uefi.py -d caihong -m 0 -r RELEASE
# 产物：Mu-caihong-0.img
```

两个坑：

- `clang` 必须在 PATH（`C:\Program Files\LLVM\bin`），否则 BootShim 编不过
- 中文 Windows 下 `edk2toollib/utility_functions.py` 会因为 GBK 输出解码崩溃，
  把 `decode(..., errors=encodingErrors)` 改成 `errors="replace"`
