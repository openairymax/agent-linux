#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/commit_msg.py — 提交信息的措辞与泄露判据
#
# 用法: python3 ci/checks/commit_msg.py [--range REV | REV..REV]
#                                      [--repo DIR] [--max-bytes 72]
#
# 判据只回答四件事，不做风格裁判：
#   P001  提交里出现本机绝对路径（家目录、挂载盘、盘符、用户配置文件）
#   P002  提交里出现把内部小问题写进历史的措辞（拼写错误、笔误一类）
#   L001  主题为空
#   L002  主题超过 --max-bytes 字节
#
# 之所以要判 P001：提交信息进入三个公开仓库，且会被机器人同步到镜像站，
# 一旦写进家目录或盘符，撤回只能改写历史——那会连带换掉发布树上的子仓指针。
# P002 判的是「低级问题不该成为永久记录」：同一件事在评审里说清楚即可，
# 不必写进历史。
#
# 主题按 UTF-8 字节计：中文一个字三字节，按字符数判会放过一倍的超长主题。
#
# 退出码: 0 合规；1 有违规；5 参数或仓库不满足。
# ===========================================================================
import argparse
import re
import subprocess
import sys

EINVAL = 5

# 每条判据一个正则。P001 只认形态完整的绝对路径，避免把 URL 里的 /home/
# 误判；P002 的中文词按原样匹配，英文 typo 按词边界匹配。
RULES = (
    ("P001", re.compile(r"/home/[A-Za-z0-9._-]+|/mnt/[A-Za-z]/"
                        r"|[A-Za-z]:\\|~/\.config"), "本机绝对路径"),
    ("P002", re.compile(r"(?i)\btypo\b|拼写错误|笔误|手误|错别字|写错"
                        r"|改错|低级错误|手滑"), "低级措辞"),
)


def usage(rc):
    """打印用法并返回给定退出码。"""
    print("用法: ci/checks/commit_msg.py [--range REV | REV..REV]\n"
          "                               [--repo DIR] [--max-bytes 72]")
    return rc


def _log(repo, rev, one):
    """跑一次 git log：one 为真只取一条，否则取 rev 覆盖的全部历史。"""
    args = ["git", "-C", repo, "log", "--no-merges",
            "--format=%H%x1f%B%x1e"]
    args += ["-1"] if one else []
    args.append(rev)
    out = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "git log 失败")
    return [c for c in out.stdout.split("\x1e") if c.strip()]


def _merge(repo, rev):
    """rev 是否为合并提交：父提交多于一个。"""
    out = subprocess.run(["git", "-C", repo, "rev-list", "--parents",
                          "-n", "1", rev], capture_output=True, text=True,
                         errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "git rev-list 失败")
    return len(out.stdout.split()) > 2


def commits(repo, rng):
    """取范围内提交的（哈希, 完整信息）。单版本号只取一条，两端式取区间。"""
    if ".." in rng:
        return _log(repo, rng, one=False)
    # -1 在遍历阶段就截断，--no-merges 之后不会再把合并提交补回来：合并
    # 检出必须先把判据落到父提交（PR 头提交），否则判的就是平台生成的
    # 合并信息。浅检出可能连父对象都没有，此时如实返回空，由调用方按
    # 「未判」处理而不是放行。
    if rng == "HEAD" and _merge(repo, rng):
        for rev in ("HEAD^2", "HEAD^1"):
            try:
                return _log(repo, rev, one=True)
            except RuntimeError:
                continue
        return []
    return _log(repo, rng, one=True)


def subject(body):
    """主题行：第一条非空行。超长与空主题都按这一行判。"""
    lines = body.strip().splitlines()
    return lines[0] if lines else ""


def inspect(body, maxb):
    """按判据逐条比对一条提交，返回（判据名, 说明）列表。"""
    subj = subject(body)
    issues = []
    if not subj:
        issues.append(("L001", "主题为空"))
    elif len(subj.encode()) > maxb:
        issues.append(("L002", f"主题 {len(subj.encode())} 字节，上限 {maxb}"))
    for code, pat, label in RULES:
        hit = pat.search(body)
        if hit:
            issues.append((code, f"{label}: {hit.group(0)}"))
    return issues


def main(argv):
    """解析参数、逐条判定、按退出码协议返回。"""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--range", dest="rng", default="HEAD")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--max-bytes", type=int, default=72)
    ap.add_argument("-h", "--help", action="store_true")
    a, extra = ap.parse_known_args(argv)
    if a.help:
        return usage(0)
    if extra or a.max_bytes <= 0:
        print(f"commit-msg: 未知或非法参数: {' '.join(extra) or a.max_bytes}",
              file=sys.stderr)
        return usage(EINVAL)
    if subprocess.run(["git", "-C", a.repo, "rev-parse", "--git-dir"],
                      capture_output=True).returncode != 0:
        print(f"commit-msg: 不是 git 仓库: {a.repo}", file=sys.stderr)
        return EINVAL
    try:
        rows = commits(a.repo, a.rng)
    except RuntimeError as exc:
        print(f"commit-msg: {exc}", file=sys.stderr)
        return EINVAL
    if not rows:
        print(f"commit-msg: 范围内没有可判提交（{a.rng}），历史可能过浅",
              file=sys.stderr)
        return EINVAL
    total = 0
    for row in rows:
        sha, _, body = row.partition("\x1f")
        for code, msg in inspect(body, a.max_bytes):
            print(f"{code} {sha[:9]} {msg}")
            total += 1
    print(f"commit-msg: {len(rows)} 条提交，{total} 处不合规")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
