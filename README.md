# UniXNU-caihong

OnePlus Pad Pro（`caihong` / OPD2404 / SM8650）上的 UniXNU 移植。

> **注意：本文档与仓库内的脚本、补丁主要由 AI 生成和整理，可能与实际设备行为存在偏差。**
>
> 所有结论都来自本机实测（屏幕日志、panic 输出、构建日志），但：
> - 硬件相关的地址、偏移、时序**只对本次使用的固件与内核版本有效**
> - 部分结论是从现象反推的（例如信任缓存格式），换 IPSW 版本可能需要重新验证
> - 未标注"已验证"的内容请当作**待确认**，不要直接用于其他设备
>
> 刷机有变砖风险，操作前请自行备份并确认分区边界。

## 当前状态

**内核已完整启动到用户态：`/sbin/launchd` 正在运行，内核日志直接输出到屏幕。**

```
ABL → BootShim → UEFI(Mu-Silicium) → UniXNU → payload(UFS/FAT32)
    → DarwinBoot → XNU → MMU → kext → AMFI → BSD init
    → 挂载 md0(恢复镜像 APFS) → load_init_program → /sbin/launchd
```

启动日志的关键几行（屏幕实拍转录）：

```
VM bootstrap done: 39 maps, 112 entries and L6 holes left
lockdown_mode: lockdown mode in nvram is off
calling sep_policy_init for AppleImage4 / AMFI / Sandbox
Security policy loaded: Apple Image4 hooks / Apple Mobile File Integrity
thread_attach: 81 kernel threads created for interface lo0
com.apple.AppleFSCompressionTypeZlib load succeeded
Added agency device md0/ram0 (02000000/0e000000) at ffffffff009374000
load_init_program: attempting to load /sbin/launchd
shared_region: 0x1a241d7c642c7d83 (1(launchd)) check_map(0x16ee/2bbe0)
```

### 已验证 / 未完成

| 项 | 状态 |
|---|---|
| UEFI 启动、UniXNU 菜单 | ✅ |
| payload 走 UFS 读取（FAT32，`CAIHONGESP` 卷标） | ✅ |
| DarwinBoot 交接（内核 + ADT + 信任缓存） | ✅ |
| XNU 内核入口、MMU、kext 全量加载 | ✅ |
| AMFI PMGR 检查 | ✅ 已 patch 绕过 |
| BSD 初始化 | ✅ |
| 根卷挂载（恢复镜像 APFS，`rd=md0`） | ✅ |
| 用户态启动（launchd） | ✅ |
| 内核日志上屏 + 心跳 | ✅ |
| 显示驱动（IOFramebuffer） | ❌ 未做，UI 需要它 |
| 输入驱动（USB / PMIC 按键） | ❌ 未做，交互需要它 |
| 完整 OS 根卷（`OS` + `Cryptex1,SystemOS`） | ⏳ `board.toml` 已配好，约 8 GB |

## 关键技术点

### 1. IPSW 组件是 IMG4 封装，不是磁盘镜像

`RestoreRamDisk` / `RestoreTrustCache` 都是 `IM4P` DER 封装（文件头 `30 84 ... IM4P`），
`hdiutil` 会直接报 "image not recognized"。用 `scripts/unwrap-img4.py` 剥壳后：

- `RestoreRamDisk` → **原始 APFS 容器**（偏移 32 是 `NXSB`，块大小 4096），
  内核可直接挂载，**不需要任何转换**
- `RestoreTrustCache` → v1 信任缓存模块

### 2. 信任缓存：IPSW 给 v1，DarwinBoot 要 v2 容器

三种格式必须分清：

| 来源 | 格式 |
|---|---|
| IPSW `RestoreTrustCache` | v1：`u32 version=1 + uuid(16) + u32 count`，条目 **22 字节** |
| IPSW `StaticTrustCache` | v2：`u32 version=2 + uuid(16) + u32 count`，条目 **24 字节** |
| DarwinBoot `ValidTrustCaches()` 要求 | **容器**：`u32 count + count 个 u32 偏移 + 每个 v2 模块` |

转换步骤：v1 → v2（22 字节条目补到 24），再包容器头，可把恢复和静态两个缓存合并进一个容器。

判定条件（照抄 DarwinBoot）：`version==2`、`size == 24 + count*24`、
cdhash 严格升序、`hash_type` 在 1..4、`start >= 4 + count*4` 且 4 字节对齐。

### 3. 根卷挂载需要 `rd=md0`

只有 `initrd` 指向 APFS 镜像还不够，内核仍报 `Failed to mount root device @IOKitBSDInit.cpp:984`。
加启动参数 `rd=md0` 强制指定内存盘为根设备后挂载成功。

### 4. AMFI 的 PMGR 检查

AMFI 在 `0xfffffff004aaecfc` 处判断 PMGR 类是否存在，不存在就 panic
（`AMFI: No PMGR?`）。反汇编确认后改成返回 0：

```
mov x0, x21 ; mov x1, x21 ; bl panic
→ mov w0, #0 ; b <正常收尾>
```

见 `scripts/patch-amfi-pmgr-name.py`（该脚本同时把 AMFI 查找的类名
`ApplePMGRNub` 改成设备树里的 `CaihongPMGRN`）。

### 5. 内核日志上屏

本机的 XNU 视频控制台从不启动，`-v` 和去掉 `serial=3` 都没用：所有 `printf`
都写进不存在的串口。解决办法是把 `printf` 钩到自写的帧缓冲绘制代码：

- `scripts/xnu-console.c` — freestanding，复用 panic 屏幕的字体与 stack-varargs
  格式化逻辑；**光标状态存在帧缓冲最后一行**（注入代码在只读段，不能用全局变量）
- `scripts/patch-xnu-console.py` — 找 `_printf` 符号，在**可执行段尾部 padding**
  里找空区注入代码，把 `_printf` 首指令改成 `b <console>`

两个必须注意的坑：

1. 代码必须放在 `__TEXT_EXEC` 段尾 padding。`__PRELINK_TEXT` 里那些大块零区
   **没有映射成可执行**，放进去会 `instruction fetch abort`。
2. 空区搜索要**从段尾往前扫**。从头扫会占用入口探针为 stage 代码和 panic 屏幕
   保留的 padding，导致 `Padding at 0x... is not empty` 构建失败。

### 6. 心跳

日志刷完停住之后无法区分"活着"和"卡死"。`processor_idle` 里对 `machine_idle`
的调用点被改成 `bl heartbeat_wrapper`：保留原调用，然后每 5 秒用 ARM 通用计数器
（`cntpct_el0`，19.2 MHz）打印一次运行秒数和内存页统计。

## 仓库内容

| 路径 | 说明 |
|---|---|
| `board.toml` | 板级描述：ADT 参数、payload 大小、IPSW 组件、`[userdata]` GPT 边界 |
| `configs/caihong_defconfig` | 内核配置 |
| `scripts/make-caihong-adt.py` | ADT 生成器（GIC / 定时器 / DRAM 基址取自 caihong 的 DTB） |
| `scripts/unwrap-img4.py` | 剥掉 IPSW 组件的 IMG4 外壳 |
| `scripts/patch-amfi-pmgr-name.py` | AMFI PMGR 检查 patch |
| `scripts/xnu-console.c` + `scripts/patch-xnu-console.py` | 内核日志上屏 + 心跳 |
| `.github/workflows/kernel.yml` | CI：拉 IPSW 组件、编内核、打 payload、上传 artifact |
| `uefi-overlay/` | Mu-Silicium 侧的改动（驱动、fdf、DTB、UniXNU Main.c），按路径覆盖即可 |

## 构建

```sh
# 内核 + payload（CI 上跑，需要 macOS：hdiutil、Xcode）
./unixnu build kernel  -D ../device
./unixnu build payload -D ../device --data-ramdisk <恢复镜像 APFS> --trust-cache <v2 容器>

# 根卷（约 8 GB，需要 macOS）
./unixnu build userdata -D ../device --ipsw <iPad_Pro_17.7.11_21H461_Restore.ipsw>
```

固件（Windows 可编）：

```bash
cd D:/uefi
export PATH="/c/Program Files/LLVM/bin:$PATH" PYTHONUTF8=1
python build_uefi.py -d caihong -m 0 -r RELEASE   # → Mu-caihong-0.img
```

## 刷入

```sh
fastboot flash userdata payload.img      # payload FAT 卷（当前 252 MiB）
fastboot boot Mu-caihong-0.img           # 固件
```

payload 必须独占 userdata；若要上完整 OS 根卷，需要先把 payload 挪到别处。

## 固件集成（已完成，保留作为参考）

参考 `tycykp/UniXNU-OnePlus-Ace5` 的 `patches/giulia-unixnu.patch`，共 8 处改动：

1. `Platforms/OnePlus/caihongPkg/DeviceBuild.py`：`PackagesPath` 加
   `"UniXNU/package"` 和 `"UniXNU/drivers/DarwinBoot"`（必须在前两行）
2. `caihong.dsc` → `[LibraryClasses]`：`UniXNULib`、`FdtLib`
3. `caihong.dsc` → `[Components]`：`CaihongInitrdDxe`、`UniXNU`、`UniXNUDxe`、`DarwinBoot`
4. `caihong.fdf` 对应位置加同样 4 个 `INF`
5. `Silicon/Silicium/SiliciumPkg/Sec/AArch64/ModuleEntryPoint.S`：
   **ABL 通过 `x0` 传进来的 FDT 指针必须在平台代码用掉 x0 之前存下来**
   ```asm
   ASM_FUNC (_ModuleEntryPoint)
     adr   x1, ASM_PFX (gSecEntryArg0)
     str   x0, [x1]
     dc    cvac, x1
     dsb   sy
   ```
6. `Sec/Sec.c`：把 `gSecEntryArg0` 发布成 HOB
7. `Sec/Sec.inf` 的 `[Guids]` 加 `gSiliciumEntryArgHobGuid`
8. `SiliciumPkg.dec` 的 `[Guids]` 定义 `gSiliciumEntryArgHobGuid`

### 额外的固件补丁：启动失败时停住

`UniXNU/package/UniXNUPkg/Library/UniXNULib/Main.c`，在 `UniXNUBootEntry` 返回错误后：

```c
if (EFI_ERROR (Status)) {
  for ( ; ;) {
    CpuDeadLoop ();
  }
}
```

否则 UniXNU 会立刻重画菜单，把 DarwinBoot 的报错（例如 `boot refused: Compromised Data`）
覆盖掉，屏幕上看不到任何线索。注意只有返回 `EFI_ERROR` 才会停；
返回 `EFI_SUCCESS` 但仍未启动的情况仍会回到菜单。

### 已知的 UniXNU 菜单问题

- **定时器只触发两三次就停**：倒计时永远卡在 `in 1 s`。
- **`timeout = 0` 在部分固件构建里不生效**：菜单画出后无限等待，且不显示倒计时文字。
- 菜单在 boot entry 返回后会置 `Countdown = FALSE`，改为无限等按键；
  若此时按键无响应就彻底卡死。

规避：把 `[boot] timeout` 设为 `0`（源码里 `RemainingMs == 0` 会直接返回默认项，
完全不使用定时器）。

## 下一步

1. **显示驱动**：把固件已在用的 GOP 帧缓冲包装成 IOKit `IOFramebuffer`，
   WindowServer 才有设备可画。工作量以周计。
2. **输入驱动**：USB 主机控制器或 PMIC 按键，交互的前提。
3. **完整根卷**：`unixnu build userdata` 生成 `OS` + `Cryptex1,SystemOS`（约 8 GB），
   刷进 userdata（`first_lba=3769068`、`last_lba=123786234`、`lba_bytes=4096`）。
   注意 payload 与根卷不能共存于 userdata。
