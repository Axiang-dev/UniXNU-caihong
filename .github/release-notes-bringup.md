## 这一版是什么

真实 OS 根卷第一次走到 `bsd_init: Calling launchd`。

根卷认证**没有通过**（EAUTH 80）——IPSW 的 `OS` 成员是封印之前的源镜像，永远过不了。
这一版靠一个**仅供 bring-up** 的补丁让内核继续，所以**不能用于日常使用，也不能发布**。

## 屏幕实测

```
root device selected: disk3s1
vfs_mountroot mounted; authentication pending
rootvp not authenticated after mounting        autherr=80
rootauth: no sealed root, continuing (autherr=80)
bsd_init: Calling launchd
load_init_program: attempting to load /sbin/launchd
shared_region: [1:launchd] vm_shared_region_start_address() failed
```

## 产物

| 文件 | 说明 |
|---|---|
| `payload-hfs.img` | 252 MiB FAT 卷：内核 + ADT + 信任缓存 + scratch-data，刷进 userdata 起始处 |

```
sha256  0bcca7db78fc1551ab370c5daea05a677969a2b6b4d01db0a40e5c2f6aaa9278
```

## 怎么用

```sh
fastboot flash userdata payload-hfs.img
fastboot boot Mu-caihong-0.img
```

内核里的 `root_match_volume_uuid` 是 `366B077F-A4FA-35DF-9A9C-B6A5CAFC8F7E`
（HFS+ 根镜像的卷身份），要和 HFS+ 根镜像配套使用；
换回 IPSW 原始 APFS 根卷时，改回该卷的 UUID 重新构建。

payload 与根镜像共用一个 userdata：payload 在 0，根镜像在 1 GiB
（`native-ufs-root-offset-mib=1024`），用 `scripts/place-root-at-offset.py` 拼。

## 已知卡点

dyld 共享缓存在 SystemOS cryptex 里，没挂上，所以 launchd 起来后立刻失败。
根卷认证 → verified block → SystemOS 挂载 → 共享缓存 是互锁的，
补丁只绕开了第一环。

下一步是 HFS+ 根镜像（把 SystemOS 合并进根卷，缓存在根卷上），
见 README「根卷与认证」一节。

## 风险

刷机会覆盖 userdata，**原有数据全部丢失**。分区边界按国行 OPD2404 写死，
别的机型不要直接刷。
