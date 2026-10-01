# -*- coding: utf-8 -*-
"""Sakura.exe 存档路径补丁: CSIDL_PERSONAL(0x05) -> CSIDL_COMMON_DOCUMENTS(0x2E).

背景: Sakura.exe 用 SHGetFolderPathA(ANSI 版) 取"我的文档"再拼
  "FAVORITE\\<标题>\\save\\", 中文用户名在 GBK/cp932 之间必乱码.
  唯一调用点: VA 0x4427cd (IAT 0x45d1f0), 参数序列经二进制确认:
    push ecx(buf?) / push ecx / push 0 / push 0 / push 5 / push 0 / call
  改法: 把 push 0x5 的 1 个字节改成 push 0x2E (公共文档, 全英文恒成立).
  不碰 HCB、不碰导入表、不加节、不改代码流, 纯 1 字节数据级补丁.

用法:
  python patch_savepath.py              # 生成 Sakura.patched.exe + 校验
  python patch_savepath.py --revert     # 从备份还原校验 (只读检查备份存在)
测试后回滚: 把 Sakura.exe.origbak 改名回 Sakura.exe 即可.
"""
from __future__ import annotations
import shutil
import struct
import sys
from pathlib import Path

GAME = Path("/mnt/d/soft/Sakura moyu/Sakura.exe")
WORK = Path("/mnt/d/soft/fvp-work")
BACKUP = WORK / "Sakura.exe.origbak"
PATCHED = WORK / "Sakura.patched.exe"

# VA 0x4427c9 是 `6a 05`(push 0x5) 的 opcode 地址; 要改的是其操作数
# fileoff(.text VA 0x401000 @ 0x400): opcode @0x41bc9, 操作数 @0x41bca
PATCH_OFF = 0x400 + (0x4427C9 - 0x401000) + 1
EXPECT_CTX = bytes.fromhex("10516a006a006a056a00ff15f0d1")
PATCH_CTX = bytes.fromhex("10516a006a006a2e6a00ff15f0d1")
CSIDL_PERSONAL, CSIDL_COMMON_DOCUMENTS = 0x05, 0x2E


def main():
    if "--revert" in sys.argv:
        assert BACKUP.is_file(), "备份不存在"
        print(f"revert target OK: {BACKUP} ({BACKUP.stat().st_size} bytes)")
        print("回滚: copy Sakura.exe.origbak -> 游戏目录/Sakura.exe")
        return
    raw = bytearray(GAME.read_bytes())
    got = bytes(raw[PATCH_OFF - 6:PATCH_OFF + 8])
    assert got == EXPECT_CTX, f"上下文不匹配 (版本不对?): {got.hex()}"
    assert raw[PATCH_OFF] == CSIDL_PERSONAL
    BACKUP.write_bytes(bytes(raw))
    print(f"已备份原文件: {BACKUP}")
    raw[PATCH_OFF] = CSIDL_COMMON_DOCUMENTS
    assert bytes(raw[PATCH_OFF - 6:PATCH_OFF + 8]) == PATCH_CTX
    # PE checksum 重算 (loader 不校验用户态 exe, 为干净起见仍更新)
    try:
        import pefile
        pe = pefile.PE(data=bytes(raw))
        pe.OPTIONAL_HEADER.CheckSum = pe.generate_checksum()
        out = pe.write()
        assert out[PATCH_OFF] == CSIDL_COMMON_DOCUMENTS
        blob = out
        print("checksum 已重算")
    except ImportError:
        blob = bytes(raw)
        print("无 pefile, 保留原 checksum (不影响加载)")
    PATCHED.write_bytes(blob)
    print(f"补丁已生成: {PATCHED} ({len(blob)} bytes)")
    print("改动: VA 0x4427c9 push 0x05 -> push 0x2E (CSIDL_COMMON_DOCUMENTS)")
    print("效果: 存档根从 C:\\Users\\<用户名>\\Documents")
    print("      改为 C:\\Users\\Public\\Documents (恒为英文, 全用户名可用)")
    print("安装: 备份游戏目录 Sakura.exe -> 复制 Sakura.patched.exe 为 Sakura.exe")


if __name__ == "__main__":
    main()
