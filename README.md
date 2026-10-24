# UniXNU-caihong

OnePlus Pad Pro（`caihong` / OPD2404 / SM8650）上的 UniXNU 移植。

> ## 📦 本项目已归档（2026-10-24）
>
> **开发已停止，仓库转为只读存档。** 内容保留下来供后续参考，不再维护、不再回复 issue。
>
> **做到哪一步**：内核完整启动到用户态，`/sbin/launchd` 正常运行，
> 内核日志与心跳直接输出到屏幕。显示驱动（IOFramebuffer）与输入驱动未实现，
> 所以**没有图形界面**——这也是停止的主要原因：从"能启动"到"能用"还差整套驱动，
> 工作量以月计。
>
> **想接着做的人**：从「下一步」一节继续，`patches/` + `uefi-overlay/` +
> `scripts/` 已经足够复现当前状态（已用干净树实测过）。
>
> 归档仓库如需恢复维护，在 GitHub 仓库页面 **Settings → Danger Zone → Unarchive** 即可。

> **注意：本文档与仓库内的脚本、补丁主要由 AI 生成和整理，可能与实际设备行为存在偏差。**
>
> 所有结论都来自本机实测（屏幕日志、panic 输出、构建日志），但：
> - 硬件相关的地址、偏移、时序**只对本次使用的固件与内核版本有效**
> - 部分结论是从现象反推的（例如信任缓存格式），换 IPSW 版本可能需要重新验证
> - 未标注"已验证"的内容请当作**待确认**，不要直接用于其他设备
>
> 刷机有变砖风险，操作前请自行备份并确认分区边界。

## ⚠️ 机型与系统兼容性（务必先读）

**本移植只在一种机器上实测过**，其他机型/系统版本**全部未验证**。
下面写清楚哪些能直接照做、哪些必须先自己核对。

### 实测环境

| 项 | 值 |
|---|---|
| 机型 | 一加平板 Pro（国行），型号 **OPD2404** |
| 代号 | `caihong` |
| SoC | 骁龙 8 Gen 3（SM8650） |
| 固件基线 | Mu-Silicium `origin/main` + 本仓库 overlay |
| 内核 | 由本仓库 CI 从 XNU 源码构建，prelink 自 iPad Pro 17.7.11 (21H461) |

### 海外版（OnePlus Pad 2 / OPD2403）能不能刷？

**未知，不要直接照抄。** 已知的对应关系：

| 版本 | 型号 | 代号 | SoC | 系统 |
|---|---|---|---|---|
| 国行 一加平板 Pro | OPD2404 | `caihong` | 骁龙 8 Gen 3 | ColorOS |
| 海外 OnePlus Pad 2 | **OPD2403** | 待确认 | 骁龙 8 Gen 3 | OxygenOS |

同代同 SoC，硬件很可能是同一块板子，但**以下三处是按国行机写死的**，
海外版必须逐项核对，任何一项不符都会写错分区或加载错误的设备树：

1. **`board.toml` 的 `[userdata] first_lba / last_lba`**
   —— 国行机的 GPT 边界（`3769068` ~ `123786234`，4096 字节 LBA）。
   海外版分区表**大概率不同**，直接刷会覆盖到别的分区。
2. **`uefi-overlay/Resources/DTBs/caihong.dtb`**
   —— ADT 生成器的 GIC / 定时器 / DRAM 基址全部来自它。
   设备树对不上，内核会在极早期挂死。
3. **`scripts/patch-amfi-pmgr-name.py` 里的地址**
   —— `0xfffffff004aaecfc` 等是**这一份内核构建**的绝对地址，
   换内核必须重新反汇编定位。

**海外版用户想尝试的话，至少先做这三件事：**

```bash
# 1. 确认代号与型号（不是 caihong / OPD2404 就不要继续）
adb shell getprop ro.product.device      # 期望 caihong
adb shell getprop ro.product.model       # 期望 OPD2404

# 2. 读出自己的 GPT，填进 board.toml（绝不能沿用仓库里的值）
adb shell su -c "cat /sys/block/sda/sda14/start; cat /sys/block/sda/sda14/size"
adb shell su -c "cat /proc/partitions"    # 确认 sda14 确实叫 userdata

# 3. 确认分区逻辑块大小是 4096（board.toml 的 lba_bytes）
adb shell su -c "cat /sys/block/sda/queue/logical_block_size"
```

三项都对上、并且自己重新生成了设备树和 ADT，才有可能跑通。
**代号不是 `caihong` 就基本可以放弃**——UEFI 的平台包、设备树、ADT 全是按它写的。

### Android 15 的用户能不能用？

**同样未验证。** 本移植只关心两件事：**ABL 能不能加载我们的固件**，
以及 **userdata 分区在哪**。系统本身是 14 还是 15 不影响 XNU 的启动逻辑，
但升级会改变下面这些：

| 升级可能带来的变化 | 影响 |
|---|---|
| GPT 分区表调整（新增/移动分区） | `board.toml` 的 LBA 失效 → **写错分区** |
| `boot` 分区大小变化 | `fastboot boot` 可能失败，但不会变砖 |
| AVB / vbmeta 策略变化 | 可能拒绝加载非签名镜像；本移植靠 `fastboot boot` 绕过 |
| ABL 版本变化 | 可能加严校验，导致固件根本起不来 |

**结论**：Android 15 本身不是障碍，但**升级后必须重新读一遍 GPT**，
确认 `userdata` 的起止 LBA 与 `board.toml` 一致再刷。
只要用 `fastboot boot`（不改 `boot` 分区）测试，最坏结果是起不来，
**不会损坏系统**；一旦 `fastboot flash userdata`，Android 数据就没了。

### 通用安全建议

1. **先只做 `fastboot boot`**，不要 `flash` —— 这样随时能回 Android
2. 动 `flash` 之前，先用 `fastboot getvar all` 和 `sgdisk --print` 记录原始分区表
3. 本移植会**覆盖 `userdata`**（Android 的用户数据全部丢失），做好备份
4. 想回 Android：`fastboot flash boot <原 boot.img>` + `fastboot erase userdata`

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
fastboot flash userdata payload.img      # payload FAT 卷，大小见 board.toml 的 size_mib
fastboot boot Mu-caihong-0.img           # 固件
```

payload 必须独占 userdata；若要上完整 OS 根卷，需要先把 payload 挪到别处。

## 固件集成

完整步骤见 [`uefi-overlay/README.md`](uefi-overlay/README.md)：打
`patches/uefi-integration.patch` 到 Mu-Silicium，再覆盖 `uefi-overlay/`，
最后把 UniXNU 源码放到 `UniXNU/`。这条流程已实测能从上游还原出可用的树。

参考项目 `tycykp/UniXNU-OnePlus-Ace5` 里的 `patches/giulia-unixnu.patch`
（**另一个仓库的文件，不在本仓库**）是最初的依据。

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

## 致谢

感谢 **Osilvfe** 大佬的指点与帮助，这个项目能走到这一步离不开他的建议。

也感谢 UniXNU、Mu-Silicium、DarwinBoot 的作者和维护者。
