# UniXNU-caihong

OnePlus Pad Pro（`caihong` / OPD2404 / SM8650）上的 UniXNU 移植。

> ## 🚧 开发中（2026-10-29 更新）
>
> 仓库曾在 2026-10-24 归档，之后又继续推进并拿到关键突破，现已恢复维护。
>
> **这两天做完的**：
>
> - 真实 OS 根卷挂载成功后，内核卡在 `rootvp not authenticated after mounting`（EAUTH 80）
> - 加了一个**仅供 bring-up** 的补丁让内核继续，屏幕实测进入 `bsd_init: Calling launchd`
> - 现在卡在 `vm_shared_region_start_address() failed`——dyld 共享缓存不在根卷上
> - 改走 HFS+ 根镜像（把 SystemOS 合并进根卷），镜像结构已离线校验通过，待上机验证
>
> 细节见下文「[根卷与认证](#根卷与认证)」。
> **bring-up 补丁绕过了根卷校验，不可用于发布。**

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

**内核能完整启动到用户态，`/sbin/launchd` 已被调起。** 目前有两条根卷路径，进度不同：

| 根卷 | 到哪一步 | 卡点 |
|---|---|---|
| 桩根卷 `rd=md0`（恢复镜像 APFS） | launchd 加载、日志上屏 | 需把 `rd=md0` 加进 `[payload] kernel_args`；没有真实系统文件，只能验证内核链路 |
| 真实 OS 根卷（IPSW 的 `OS` 成员） | 挂载成功 → 认证失败 → 补丁放行 → `Calling launchd` | `vm_shared_region_start_address() failed` |

```
ABL → BootShim → UEFI(Mu-Silicium) → UniXNU → payload(UFS/FAT32)
    → DarwinBoot → XNU → MMU → kext → AMFI → BSD init
    → 挂载根卷 → rootauth → load_init_program → /sbin/launchd
```

桩根卷（`rd=md0`）的启动日志（屏幕实拍转录）：

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

真实 OS 根卷的启动日志（2026-10-27 实拍转录）：

```
root device selected: disk3s1
vfs_mountroot mounted; authentication pending
rootvp not authenticated after mounting        ← autherr=80（EAUTH）
rootauth: no sealed root, continuing (autherr=80)     ← bring-up 补丁，见下文
bsd_init: Calling launchd
load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development
load_init_program: attempting to load /sbin/launchd
shared_region: [1:launchd] check_rp(...) vm_shared_region_start_address() failed
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
| 根卷挂载（真实 OS 成员，UFS 窗口） | ✅ 挂载成功 |
| 根卷认证 | ❌ EAUTH 80，靠 bring-up 补丁放行 |
| 用户态启动（launchd） | ✅ 已调起（`bsd_init: Calling launchd`） |
| dyld 共享缓存 | ❌ `vm_shared_region_start_address() failed` |
| HFS+ 根镜像（OS + SystemOS 合并） | ⏳ 镜像已就绪、结构已校验，待上机 |
| 内核日志上屏 + 心跳 | ✅ |
| 显示驱动（IOFramebuffer） | ❌ 未做，UI 需要它 |
| 输入驱动（USB / PMIC 按键） | ❌ 未做，交互需要它 |

## 根卷与认证

### 为什么 IPSW 里的 OS 卷永远过不了认证

IPSW 的 `OS` 成员是**封印之前**的源镜像（卷名 `newfs_apfs`，刚格式化出来的），
没有 `com.apple.os.update` 快照，也没有密封签名。`bsd_init` 的检查因此必然失败：

```
rootvp not authenticated after mounting        （autherr=80，即 EAUTH）
```

这不是读法或配置的问题——镜像本身没有封印，怎么读都过不了。
要过只有两条路：**用机主自己的认证（owner-root）**，或者**换一个没有封印概念的文件系统**。

### 参考实现各自走到哪

| 项目 | 做法 | 公开产物 |
|---|---|---|
| `UniXNU-sheng` | owner-root（自签策略包） | 策略包手工在 macOS 准备，CI 里没有这一步 |
| `UniXNU-Pagani_SM8750` | owner-root，**已到系统图形界面** | `STAGE6.md` 记录了全过程 |
| `UniXNU-uke` | 未启用 owner-root | 配置齐全，但归档列表里没有对应步骤，启动用的不是真实系统根卷 |
| `UniXNU-OnePlus-Ace5` | HFSX 根卷（没有封印概念） | 到 Hello / 设置界面，触控可用 |
| 本仓库 | 先绕过认证，再转 HFS+ | 见下 |

Pagani 的审计行说明了放行条件：

```
NATIVE_ROOT binding: apple=80 owner=1 readonly=1 mounted=1000006 selected=1000006
```

`apple=80`（EAUTH）失败，但 `owner=1` 且只读 → 放行。**关键在 owner 查询能返回 1。**

### bring-up 补丁（不可发布）

本节和下面提到的两个补丁都在**上游 UniXNU 仓库**的 patches/xnu 目录里（本仓库不含它们），
构建时由本仓库的钩子脚本在拉下来的上游树里改写。

`offline-owner-root-binding-audit.patch` 里的 panic 被换成打印：

```c
-  panic("rootvp not authenticated after mounting");
+  printf("rootauth: no sealed root, continuing (autherr=%d)\n", autherr);
```

由 `.github/scripts/patch-xnu-source.py` 的 `patch_rootauth_message()` 在构建时改写，
CI 里有断言 `strings kernel | grep "rootauth: no sealed root"`，没进内核就直接失败。

两个坑：

- **不能用 `bsd_init_kprintf` 打这行日志**。它在非调试配置下是空宏
  （`#define bsd_init_kprintf(x, ...)`），整句会被编译掉，看起来像补丁没生效。
- **不能另加一个补丁去改这一行**。构建脚本对每个补丁做 `git apply --reverse --check`，
  后面的补丁改掉前面的上下文，会让前面的补丁既不正向也不反向可应用。
  要改就改**拥有这行的那个补丁本身**，并把 context 行拆成 `-`/`+` 对（hunk 计数不变）。

它只是让内核别停：`native_verified_block_root_authorized()` 仍然返回 0。

### 卡点：dyld 共享缓存不在根卷上

launchd 被调起后立刻失败：

```
shared_region: [1:launchd] check_rp(...) vm_shared_region_start_address() failed
```

iOS 16+ 的 dyld 共享缓存在 SystemOS cryptex 里
（`/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld/`），不在根卷上。
`offline-owner-systemos-mount.patch`（同样在上游）想挂它，但开头就是：

```c
root_authorized = ... && native_verified_block_root_authorized(root_device);
if (!root_authorized) goto done;
```

所以整条链是互锁的：

```
根卷认证 → verified block → SystemOS 挂载 → dyld 共享缓存 → launchd
```

补丁能绕开"认证失败"，绕不开它后面这些依赖。**主线还是要真正把根卷做成"可认证"或"无需认证"。**

### 正在走的路：HFS+ 根镜像

HFS+ 没有"密封系统卷"这个概念。把 OS + SystemOS 的文件合并进一个 HFS+ 卷，
共享缓存就直接落在 `/System/Library/Caches/com.apple.dyld/`，不必挂 cryptex。

| 文件 | 作用 |
|---|---|
| `.github/workflows/iosroot.yml` | 下载固定 IPSW → 校验两个 DMG → `newfs_hfs -s -J` → rsync → 合并 SystemOS → 输出 `root.img.gz` + `volume-uuid.txt` |
| `scripts/pack-hfs-userdata.py` | 把根镜像包成驱动读的 SHNGROOT |
| `scripts/build-userdata-gpt.py` | payload + 两个窗口合成一个 userdata（OS 窗口带 GPT 外壳） |
| `scripts/place-root-at-offset.py` | payload 放 0、根镜像放 1 GiB（`native-ufs-root-offset-mib`） |

#### 三个必须知道的约束（都是实测踩出来的）

1. **SHNGROOT 必须同时有 `OS` 和 `SystemOS` 两个窗口。**
   驱动 `ufsBuildRootPlan` 里 `if (system == count || os == count) return false;`，
   缺一个就不发布任何设备，内核只会报 `Failed to mount root device`。
2. **窗口要包在 GPT 里。** 驱动通过分区方案发布设备，裸文件系统没有分区 GUID 可匹配。
   GPT 里的分区 GUID 就填 `board.toml` 的 `root_match_volume_uuid`——我们把分区 GUID
   直接设成根镜像的卷 UUID，这样按分区匹配、按卷匹配都能对上。
3. **GPT 头 CRC 只覆盖前 92 字节**（`header_size`），不是整个扇区。算错整张分区表失效，
   内核侧表现同样是"找不到根设备"。
4. **窗口偏移是绝对值，必须等于实际写入位置。** 头部里的 `at` 不是相对量，算错一点，
   内核读到的就是空白区域，表现还是"找不到根设备"。`build-userdata-gpt.py` 现在按
   写入位置取偏移，写完回读校验；`place-root-at-offset.py` 遇到 SHNGROOT 镜像会直接拒绝。

> **重建镜像会换掉 UUID。** `newfs_hfs` 每次生成的卷 UUID 都不同，`iosroot` 工作流会把
> 新值写进 `volume-uuid.txt`。拿到新镜像后必须**同时**改两处：`board.toml` 的
> `root_match_volume_uuid`，以及 `build-userdata-gpt.py --os-guid`。
> 只改一处的结果是"镜像内容没错、就是找不到根设备"。

#### 当前镜像状态

```
OS 窗口：GPT 磁盘
  分区 GUID  366B077F-A4FA-35DF-9A9C-B6A5CAFC8F7E
  分区类型  Apple HFS (48465300-…)
  内容      HFSX 16 GiB（OS + SystemOS 已合并）
SystemOS 窗口：3.3 GB，sha256 b4908cde…（与 IPSW 一致）
```

离线校验已通过：GPT 签名、头 CRC、条目 CRC、分区内 HFSX 卷头（`HX` v5），
以及两个窗口的偏移回读（数据确实在头部记录的位置上，SystemOS 的 sha256 与 IPSW 一致）。
**待验证**：设备上能否匹配到这个分区并挂载。

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

走真实 OS 根卷时**不加** `rd=md0`，根设备由 UFS 窗口发布（`disk3s1`）；
同一个内核两条路都能用，区别只在启动参数和 userdata 布局。

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
| `scripts/pack-hfs-userdata.py` | 把 HFS+ 根镜像包成 SHNGROOT（驱动读的格式） |
| `scripts/build-userdata-gpt.py` | payload + OS/SystemOS 两窗口合成 userdata，OS 窗口带 GPT 外壳 |
| `scripts/place-root-at-offset.py` | 裸文件系统镜像专用：payload 放 0、根镜像放 1 GiB |
| `.github/workflows/kernel.yml` | CI：拉 IPSW 组件、编内核、打 payload、上传 artifact |
| `.github/workflows/iosroot.yml` | CI：从固定 IPSW 组装 HFS+ 根镜像（OS + SystemOS 合并） |
| `.github/workflows/userdata.yml` | CI：用 hdiutil 组装 APFS 根卷（仅 macOS） |
| `.github/workflows/verify.yml` | 每次 push 跑 `scripts/verify.py`，检查引用与配置是否自洽 |
| `LICENSE` | BSD-2-Clause-Patent，与上游 UniXNU 一致 |
| `uefi-overlay/` | Mu-Silicium 侧的改动（驱动、fdf、DTB、UniXNU Main.c），按路径覆盖即可 |

## 构建

```sh
# 内核 + payload（CI 上跑，需要 macOS：hdiutil、Xcode）
./unixnu build kernel  -D ../device
./unixnu build payload -D ../device --data-ramdisk <恢复镜像 APFS> --trust-cache <v2 容器>

# 根卷（约 8 GB，需要 macOS）
./unixnu build userdata -D ../device --ipsw <iPad_Pro_17.7.11_21H461_Restore.ipsw>
```

HFS+ 根镜像这条线（`iosroot` 工作流产出 `root.img.gz` 后）：

```sh
gunzip -c root.img.gz > root.img

# 1) 包成 SHNGROOT（单窗口，仅用于检查内容）
python scripts/pack-hfs-userdata.py --root root.img --output userdata-hfs.img

# 2) 和 payload、SystemOS 一起合成带 GPT 的 userdata（推荐，一次成型）
python scripts/build-userdata-gpt.py --payload payload.img \
    --os-image root.img --os-guid <board.toml 的 root_match_volume_uuid> \
    --systemos systemos-raw.img --output userdata.img
```

`build-userdata-gpt.py` 写完会把两个窗口回读校验一遍，偏移对不上直接失败。

`place-root-at-offset.py` 是另一条路：把一个**裸文件系统**镜像整体右移、payload 放开头。
它**不能**用在 SHNGROOT 镜像上（窗口存的是绝对偏移，移完就全错位），脚本会直接拒绝。

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

payload 和根镜像**可以共用一个 userdata**，靠偏移分开：

```
userdata
  0            payload FAT（固件从这里挂 FAT16）
  1 GiB        SHNGROOT 头 + OS / SystemOS 两个窗口（native-ufs-root-offset-mib=1024）
```

`board.toml` 的 `[userdata] first_lba / last_lba / lba_bytes` 必须和本机 GPT 一致，
否则会写错分区。20 GB 的镜像 USB 大约 9 分钟；中途中断后设备可能卡在异常状态，
拔插数据线、重进 fastboot 即可恢复。

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

按优先级：

1. **验证 HFS+ 根卷能否被匹配并挂载**。镜像已经做好、结构也离线校验过，
   缺的就是上机那一步。若挂上且不再报 `vm_shared_region_start_address()`，
   launchd 就能拿到共享缓存继续往下走。
2. **若 HFS+ 仍不行，回到 owner-root**。Pagani 已经用这条路到图形界面，
   要素是：`NativeUfs` / `NativeVirtioBlock` 都用**普通模式**编进内核（只提供查询函数，
   不建 VirtIO 设备节点）、`CONFIG_XNU_NATIVE_PAN=y`、策略包按 `os` + `systemos` 两份生成、
   卷 UUID 用 APFS 卷身份。
3. **显示驱动**：把固件已在用的 GOP 帧缓冲包装成 IOKit `IOFramebuffer`，
   WindowServer 才有设备可画。工作量以周计。
4. **输入驱动**：USB 主机控制器或 PMIC 按键，交互的前提。

## 许可

本仓库的脚本、配置与文档以 **BSD-2-Clause-Patent** 提供，与上游 UniXNU 一致，全文见 `LICENSE`。

内核补丁作用于 Apple 的 XNU 源码，那部分仍遵循 XNU 自身的许可（APSL 2.0）；
Mu-Silicium、DarwinBoot 等组件各自保留原许可。再分发时请一并保留这些声明。

## 致谢

感谢 **Osilvfe** 大佬的指点与帮助，这个项目能走到这一步离不开他的建议。

也感谢 UniXNU、Mu-Silicium、DarwinBoot 的作者和维护者。
