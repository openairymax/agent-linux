#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# hwcap.py — 五类硬件能力判定与按能力选机（V-10 / HW-01、HW-02）
#
# 目标机是「285H 类笔记本」，但判定不能按机型名。机型名只能说明厂商怎么
# 命名，说明不了这台机器上有没有可用的无线网卡、声卡、键盘与背光。本模块
# 只读 sysfs/procfs 的能力节点，五类各给一个结论：
#
#   display    KMS 驱动 + 已连接连接器 + 可枚举模式 + 非空 EDID
#   wireless   注册了 ieee80211 PHY 且有挂在其下的网络接口
#   audio      有 sound card 且至少一个 PCM 节点
#   input      有输入设备且至少一个是键盘（capabilities/key 含 KEY_A）
#   backlight  背光节点存在且 max_brightness 为正、brightness 可读
#
# 每类结论只有三种：pass、n/a、fail。n/a 必须带 note，说明是「本机无此
# 硬件」还是「硬件在但缺内核能力」；后者要写明顺延版本。fail 表示硬件
# 在位但判据不成立。任何一类都不允许静默跳过——缺证据也要落一行。
#
# 选机只看这五个结论：--require 列出的类必须全部 pass，任一 n/a 或 fail
# 即拒绝。判定链里没有 DMI、机型名或品牌白名单，换机器不改代码。
#
#   hwcap.py [--root DIR] [--class LIST] [--require LIST] [--json]
#
# 退出码：0 无 fail 且选机通过；1 有 fail 或选机拒绝；5 参数不满足。
# ===========================================================================

import argparse
import json
import os
import sys
from pathlib import Path

EINVAL = 5
CLASSES = ("display", "wireless", "audio", "input", "backlight")
KEY_A = 30


class Parser(argparse.ArgumentParser):
    """参数错误统一退 5，与各门禁的 EINVAL 约定一致。"""

    def error(self, message):
        sys.stderr.write("参数错误: %s\n" % message)
        raise SystemExit(EINVAL)


def read(path):
    """读一个 sysfs/procfs 文本节点；不可读按空串处理，由判据决定含义。"""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def nodes(base, pattern):
    try:
        return sorted(Path(base).glob(pattern))
    except OSError:
        return []


def drv_name(card):
    """取 DRM 卡绑定的驱动名；无绑定返回空串。"""
    try:
        link = os.path.realpath(str(Path(card) / "device" / "driver"))
        return Path(link).name
    except OSError:
        return ""


def gpu_bus(root):
    """PCI 基类 0x03 表示显示设备，用于区分「无显示硬件」与「未绑定驱动」。"""
    return any(read(dev / "class").startswith("0x03")
               for dev in nodes(root / "sys/bus/pci/devices", "*"))


def drm_nodes(root):
    """卡节点与连接器节点；Writeback 不是可扫描输出，卡节点名不含短横。"""
    drm = root / "sys/class/drm"
    cards = [p for p in nodes(drm, "card[0-9]*") if "-" not in p.name]
    conns = [p for p in nodes(drm, "card*-*") if "Writeback" not in p.name]
    return cards, conns


def no_display(root, cards):
    """无 card 节点不足以判「本机无显示硬件」：驱动未绑定时同样缺卡节点。

    只有 PCI 显示类设备与 fb0 都不存在，才说明硬件不在位；否则结论必须
    落在 fail，让「硬件在但缺能力」与「没有这类硬件」分得开。
    """
    if cards:
        return False
    return not gpu_bus(root) and not (root / "sys/class/graphics/fb0").exists()


def kms_drivers(cards):
    """已绑定的 KMS 驱动名；simpledrm 只搬帧缓冲，不提供 KMS 能力。"""
    return sorted(d for d in {drv_name(c) for c in cards}
                  if d and d != "simple-framebuffer")


def tied_outputs(conns):
    """status 为 connected 的连接器。"""
    return [c for c in conns if read(c / "status") == "connected"]


def screen_state(conns, driver):
    """连通性之上的证据链：pass 需要连接器、模式与 EDID 同时成立。"""
    tied = tied_outputs(conns)
    if not tied:
        return ("fail", "no-output", "无已连接输出")
    both = [c for c in tied if read(c / "modes") and read(c / "edid")]
    if both:
        return ("pass", "driver=%s,connector=%s,edid=1" % (driver, both[0].name), "")
    if not any(read(c / "modes") for c in tied):
        return ("fail", "no-mode", "已连接输出无可枚举模式")
    return ("fail", "no-edid", "已连接输出 EDID 为空")


def class_display(root):
    """显示类按「硬件在位 → 连接器 → KMS 绑定 → 输出证据」四段判定。"""
    cards, conns = drm_nodes(root)
    if no_display(root, cards):
        return ("n/a", "no-display-hardware", "本机无显示硬件")
    if not conns:
        return ("fail", "no-connector", "有显示设备但无连接器节点")
    kms = kms_drivers(cards)
    if not kms:
        return ("fail", "no-kms", "仅 simpledrm 或未绑定 KMS 驱动")
    return screen_state(conns, kms[0])


def class_wireless(root):
    phys = nodes(root / "sys/class/ieee80211", "phy*")
    if not phys:
        return ("n/a", "no-ieee80211", "本机无无线硬件")
    nets = [p.name for p in nodes(root / "sys/class/net", "*")
            if (p / "phy80211").exists() or (p / "wireless").exists()]
    if nets:
        return ("pass", "phy=%d,net=%s" % (len(phys), nets[0]), "")
    return ("fail", "phy-without-netdev", "无线 PHY 已注册但无网络接口")


def class_audio(root):
    cards = nodes(root / "sys/class/sound", "card[0-9]*")
    if not cards:
        return ("n/a", "no-sound-card", "本机无音频硬件")
    for card in cards:
        pcm = nodes(card, "pcm*")
        if pcm:
            return ("pass", "card=%s,pcm=%d" % (card.name, len(pcm)), "")
    return ("fail", "card-without-pcm", "声卡无 PCM 节点")


def key_set(text, bit):
    """按内核 %*pb 的打印顺序取位：最后一个 64 位字是最低位。"""
    words = text.split()
    idx = len(words) - 1 - bit // 64
    if idx < 0:
        return False
    try:
        word = int(words[idx], 16)
    except ValueError:
        return False
    return bool(word >> (bit % 64) & 1)


def class_input(root):
    devs = nodes(root / "sys/class/input", "input[0-9]*")
    if not devs:
        return ("n/a", "no-input-device", "本机无输入设备")
    keys = [d for d in devs if key_set(read(d / "capabilities/key"), KEY_A)]
    if keys:
        return ("pass", "devices=%d,keyboards=%d" % (len(devs), len(keys)), "")
    return ("fail", "input-without-keyboard", "有输入设备但无键盘")


def usable(node):
    mx = read(node / "max_brightness")
    return mx.isdigit() and int(mx) > 0 and (node / "brightness").exists()


def class_backlight(root):
    scr = nodes(root / "sys/class/backlight", "*")
    kbd = nodes(root / "sys/class/leds", "*kbd_backlight*")
    if not scr and not kbd:
        return ("n/a", "no-backlight", "本机无背光设备")
    good = [n.name for n in scr + kbd if usable(n)]
    if good:
        return ("pass", "node=%s,kbd=%d" % (good[0], len(kbd)), "")
    return ("fail", "backlight-unusable", "背光节点不可读写")


CLASS_FN = {
    "display": class_display,
    "wireless": class_wireless,
    "audio": class_audio,
    "input": class_input,
    "backlight": class_backlight,
}


def pick(text):
    """解析能力类列表；空串表示「未指定」，由调用方决定缺省语义。"""
    if not text:
        return []
    want = [p.strip() for p in text.split(",") if p.strip()]
    bad = [p for p in want if p not in CLASSES]
    if bad:
        sys.stderr.write("参数错误: 未知能力类: %s\n" % ",".join(bad))
        raise SystemExit(EINVAL)
    return [c for c in CLASSES if c in want]


def evaluate(root, only):
    return [(cid,) + CLASS_FN[cid](root) for cid in only]


def emit(rows, need, as_json):
    summary = {"pass": 0, "n/a": 0, "fail": 0}
    for row in rows:
        summary[row[1]] += 1
    missing = [r[0] for r in rows if r[0] in need and r[1] != "pass"]
    accept = not missing
    if as_json:
        print(json.dumps({
            "classes": [{"id": r[0], "verdict": r[1],
                         "evidence": r[2], "note": r[3]} for r in rows],
            "summary": summary,
            "select": {"require": need, "accept": accept, "missing": missing},
        }, ensure_ascii=False))
    else:
        for cid, verdict, evidence, note in rows:
            label = "N/A" if verdict == "n/a" else verdict.upper()
            line = "CLASS %s %s %s" % (cid, label, evidence)
            if note:
                line += " note=%s" % note
            print(line)
        print("HW-MATRIX pass=%d na=%d fail=%d"
              % (summary["pass"], summary["n/a"], summary["fail"]))
        if need:
            print("SELECT machine %s%s"
                  % ("accept" if accept else "reject",
                     "" if accept else " missing=" + ",".join(missing)))
    ok = summary["fail"] == 0 and accept
    return 0 if ok else 1


def main(argv):
    ap = Parser(prog="hwcap.py")
    ap.add_argument("--root", default="/")
    ap.add_argument("--class", dest="only", default="")
    ap.add_argument("--require", default="")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    root = Path(ns.root)
    if not root.is_dir():
        sys.stderr.write("参数错误: --root 目录不存在: %s\n" % ns.root)
        return EINVAL
    rows = evaluate(root, pick(ns.only) or list(CLASSES))
    return emit(rows, pick(ns.require), ns.json)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
