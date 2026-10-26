#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
pick_image.py — 按提交认领已被验收的安装镜像

安装镜像由 image-build.yml 在同一提交上组装并验收：A 轮产出产品 ISO 并断言
AIRYMAX-DESKTOP-READY，B 轮产出验收 ISO 并断言 AIRYMAX-SMOKE-PASS。发行
流程（0.0.3-验收发行方案 §3.6 方式 2）在 tag 前由发布负责人认领 A 轮产物
并人工附加；若在 tag 上重新组装镜像，「发布的镜像」与「验收过的镜像」就不
是同一份字节，G-ISO-1 与 G-UKUI-1 随之失去意义，且多花一小时以上构建时间。
本工具把「认领哪一次运行」从人工辨认变为可复算的判据。

认领判据（三条同时成立）：
  1. 制品名等于 --name。产品 ISO 的制品名为 airymaxos-image，B 轮验收 ISO
     的制品名带 -acceptance 后缀，故验收镜像天然不会被认领——这是「发行物
     不得含自动登录与冒烟夹具」的结构性保证，而非人工约定。
  2. 制品未过期。GitHub 制品过期后不可下载，认领即失败。
  3. workflow_run.head_sha 等于发布提交。不同提交的镜像即使同名也不可用。

多条命中取 created_at 最新者：同一提交重跑时以最后一次组装为准。

Usage:
    pick_image.py --artifacts artifacts.json --sha <commit>
    pick_image.py --artifacts artifacts.json --sha <commit> \\
        --output "$GITHUB_OUTPUT"        # 追加 run_id= 与 artifact=

Exit codes:
    0 — 命中并输出
    1 — 无制品满足判据
    2 — 用法或输入错误（文件不可读、JSON 非法、缺字段）

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "EXIT_MISS",
    "EXIT_OK",
    "EXIT_USAGE",
    "MissError",
    "ToolError",
    "build_parser",
    "emit",
    "is_cand",
    "pick",
    "read_list",
]

EXIT_OK = 0
EXIT_MISS = 1
EXIT_USAGE = 2
DEFAULT_NAME = "airymaxos-image"


class ToolError(Exception):
    """输入或环境错误，映射到退出码 2。"""


class MissError(ToolError):
    """无制品满足认领判据，映射到退出码 1。"""


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。

    Returns:
        配置好的 ArgumentParser。
    """
    parser = argparse.ArgumentParser(
        description="从 GitHub artifacts 列表认领已被验收的安装镜像")
    parser.add_argument("--artifacts", required=True,
                        help="GitHub artifacts 列表 JSON "
                             "（actions/artifacts 的响应体）")
    parser.add_argument("--sha", required=True,
                        help="发布提交 SHA（tag 指向的提交）")
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help=f"制品名（默认：{DEFAULT_NAME}）")
    parser.add_argument("--output", default=None,
                        help="追加写入的 key=value 文件（如 $GITHUB_OUTPUT）")
    return parser


def read_list(path: Path) -> list[Any]:
    """读取制品列表，兼容 `{"artifacts": [...]}` 与裸数组两种形态。

    Args:
        path: JSON 文件路径。

    Returns:
        制品条目列表。

    Raises:
        ToolError: 文件不可读、JSON 非法或结构不符。
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolError(f"无法读取制品列表 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolError(f"制品列表不是合法 JSON: {exc}") from exc
    if isinstance(doc, dict):
        doc = doc.get("artifacts", [])
    if not isinstance(doc, list):
        raise ToolError("制品列表结构不符：期望数组或含 artifacts 数组的对象")
    return doc


def is_cand(item: Any, name: str, sha: str) -> bool:
    """判断单个制品是否满足认领判据。

    Args:
        item: 制品条目。
        name: 期望的制品名。
        sha: 发布提交 SHA。

    Returns:
        名称、过期状态、提交与运行 id 四项同时合规时为 True。
    """
    if not isinstance(item, dict):
        return False
    run = item.get("workflow_run")
    if not isinstance(run, dict):
        return False
    return (item.get("name") == name
            and item.get("expired") is False
            and run.get("head_sha") == sha
            and isinstance(item.get("id"), int)
            and isinstance(run.get("id"), int))


def pick(items: Sequence[Any], name: str, sha: str) -> dict[str, Any]:
    """从制品列表中选出唯一可认领的制品。

    Args:
        items: 制品条目序列。
        name: 期望的制品名。
        sha: 发布提交 SHA。

    Returns:
        被选中的制品条目。

    Raises:
        MissError: 无制品满足判据。
    """
    cand = [item for item in items if is_cand(item, name, sha)]
    if not cand:
        raise MissError(
            f"提交 {sha} 上没有可认领的制品 {name}：发布前须先让 image-build 在"
            f"同一提交上完成组装与验收（0.0.3-验收发行方案 §5.3 步骤 3）")
    return max(cand, key=lambda item: (str(item.get("created_at", "")), item["id"]))


def emit(item: dict[str, Any], output: str | None) -> None:
    """输出运行 id 与制品名，可直接写入 `$GITHUB_OUTPUT`。

    Args:
        item: 被选中的制品条目。
        output: 追加写入的文件路径；None 表示只写标准输出。
    """
    text = f"run_id={item['workflow_run']['id']}\nartifact={item['name']}\n"
    if output:
        with open(output, "a", encoding="utf-8") as sink:
            sink.write(text)
    sys.stdout.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数序列；None 表示使用 `sys.argv[1:]`。

    Returns:
        进程退出码（0 命中 / 1 未命中 / 2 用法或输入错误）。
    """
    args = build_parser().parse_args(argv)
    try:
        item = pick(read_list(Path(args.artifacts)), args.name, args.sha)
    except MissError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_MISS
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    emit(item, args.output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
