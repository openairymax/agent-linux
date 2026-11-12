#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""attest_check.py — 校验 in-toto / SLSA v1 构建证明

为什么单独成文件：校验器同时服务三处——生成后自检、发布前复核、装机侧
反查。任何一处走不同实现都会让「证明有效」出现两种口径，因此判据只允许
有一份。内嵌在 gate 的 heredoc 里时它既不能被单测直接覆盖，也无法按证据
链分段阅读；做成模块之后，每段判据一个函数，入口只负责顺序与退出码。

判据按证据链分段：
  1. 文档类型：_type 必须是 in-toto Statement v1，predicateType 必须是
     SLSA provenance v1——换了文档类型，后面的字段名都不再具有意义。
  2. subject 集合：逐项文件名非空、sha256 合法、不重名；给出对照清单时
     还要求名字集合相等且逐项摘要相等。跳过某一项与改掉某一项同属一类
     错误，不能只查「清单里的都对得上」。
  3. 构建信息：buildId 形状合法且不是占位值 unset，与本轮期望一致，
     并且证明里有源码提交——没有提交的证明回答不了「从哪来」。

退出码: 0 证明有效；1 证明无效；2 用法错误。
"""

import json
import re
import sys

STMT_TYPE = "https://in-toto.io/Statement/v1"
PRED_TYPE = "https://slsa.dev/provenance/v1"
DIGEST = re.compile(r"[0-9a-f]{64}")
BUILD_ID = re.compile(r"[A-Za-z0-9._+-]{1,64}")
MISS = "-"
LIMIT = 3


def load(path):
    """读证明；缺失或不是 JSON 都属于「证明无效」，不属于用法问题。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        print("证明不可读: %s (%s)" % (path, exc), file=sys.stderr)
        sys.exit(1)


def check_doc(st, errs):
    if st.get("_type") != STMT_TYPE:
        errs.append("_type 不是 in-toto Statement v1")
    if st.get("predicateType") != PRED_TYPE:
        errs.append("predicateType 不是 SLSA v1")


def scan_subject(st, label, errs):
    """逐项收进 {文件名: 摘要}；重名本身就是错，不做后写覆盖前写。"""
    have = {}
    for item in st.get("subject") or []:
        name = item.get("name") or ""
        digest = (item.get("digest") or {}).get("sha256") or ""
        if not name:
            errs.append("subject 缺文件名字")
        elif not DIGEST.fullmatch(digest):
            errs.append("%s 缺合法 sha256: %s" % (label, name))
        elif name in have:
            errs.append("%s 重复出现: %s" % (label, name))
        else:
            have[name] = digest
    return have


def scan_want(subj_file):
    """对照清单是「名字\\t摘要」逐行；缺省时返回空表，由调用方决定是否比对。"""
    want = {}
    if not subj_file:
        return want
    with open(subj_file, encoding="utf-8") as fh:
        for line in fh:
            name, digest = line.rstrip("\n").split("\t")
            want[name] = digest
    return want


def cmp_sets(have, want, label, errs):
    """集合相等且逐项摘要相等；报告只列前三项，够定位又不刷屏。"""
    if set(have) != set(want):
        missing = sorted(set(want) - set(have))[:LIMIT]
        extra = sorted(set(have) - set(want))[:LIMIT]
        errs.append("%s 集合与证明不符（缺 %s，多 %s）"
                    % (label, missing or MISS, extra or MISS))
    for name in sorted(set(have) & set(want)):
        if have[name] != want[name]:
            errs.append("%s 摘要与证明不符: %s" % (label, name))


def check_build(pred, bid, errs):
    """buildId 是本轮自述，必须形状合法、非占位、与期望一致。"""
    internal = (pred.get("buildDefinition") or {}).get("internalParameters") or {}
    got = internal.get("buildId") or ""
    if not BUILD_ID.fullmatch(got) or got == "unset":
        errs.append("buildId 非法: %r" % got)
    if bid and got != bid:
        errs.append("buildId 不符: 期望 %s，证明为 %s" % (bid, got))
    if not internal.get("commit"):
        errs.append("缺少源码提交")
    return got


def report(have, got):
    subject = (",".join(have.values())[:64] if len(have) == 1
               else "%d 项" % len(have))
    print("AIRYMAX-ATTEST-PASS buildId=%s subject=%s name=%s"
          % (got, subject, ",".join(sorted(have)[:3])))


def run(argv):
    path, subj_file, bid, kind = argv
    st = load(path)
    errs = []
    check_doc(st, errs)
    pred = st.get("predicate") or {}
    params = (pred.get("buildDefinition") or {}).get("externalParameters") or {}
    kind = params.get("kind") or kind
    label = "ISO" if kind == "iso" else "资产"
    have = scan_subject(st, label, errs)
    if subj_file:
        cmp_sets(have, scan_want(subj_file), label, errs)
    if not have:
        errs.append("%s 集合为空" % label)
    got = check_build(pred, bid, errs)
    if not errs:
        report(have, got)
        return 0
    for err in errs:
        print("证明无效: " + err, file=sys.stderr)
    return 1


def main(argv):
    if len(argv) != 5:
        print("用法: attest_check.py FILE [SUBJECT_FILE] [BUILD_ID] [KIND]",
              file=sys.stderr)
        return 2
    return run(argv[1:5])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
