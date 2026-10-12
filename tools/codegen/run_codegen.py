#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""run_codegen — AirymaxOS 契约代码生成管道统一入口。

按依赖顺序编排 tools/codegen 下的 4 个契约生成器，将 R-01 SSoT 契约源
确定性渲染为多语言绑定产物（06-codegen-pipeline.md §6.3）：

    cap_gen  →  bitfield_gen  ∥  ipc_gen  →  syscall_gen

依赖序由 codegen_types.PIPELINE_ORDER 唯一定义。本模块只做编排——导入阶段
模块、调用其 generate/check 入口、依返回码短路——不承载任何生成逻辑。
任一阶段失败即透传其非零返回码，保证 CI（§9.3）能在契约源或生成产物
漂移时阻断合并。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from codegen_types import ExitCode, GeneratorName, PIPELINE_ORDER

GENERATE_ENTRY = "generate"

# §6.3 GENERATORS 表：阶段说明 + 零漂移校验入口（依赖序由 PIPELINE_ORDER 唯一定义）
STAGES: dict[GeneratorName, tuple[str, tuple[str, ...]]] = {
    GeneratorName.CAP: ("Capability ID 生成（LSM 钩子静态注册，非 codegen）", ("check",)),
    GeneratorName.BITFIELD: ("位域结构生成", ("check",)),
    GeneratorName.IPC: ("IPC 消息头生成", ("check",)),
    GeneratorName.SYSCALL: (
        "syscall 多语言绑定生成（依赖 cap_gen 输出）",
        ("check", "check_tbl"),
    ),
}


def default_repo_root() -> Path:
    """agent-linux/ 仓库根（tools/codegen/ 上溯两级）。"""
    return TOOLS_DIR.parents[1]


def _invoke(module: object, entry: str, repo_root: Path) -> int:
    """调用阶段模块的具名入口，缺少入口时返回失败码。"""
    func = getattr(module, entry, None)
    if func is None:
        print(f"ERROR: {module.__name__} missing entry '{entry}'", file=sys.stderr)
        return int(ExitCode.FAILURE)
    return int(func(repo_root))


def run_pipeline(repo_root: Path, *, check: bool) -> int:
    """按 PIPELINE_ORDER 编排全部阶段；check=True 走零漂移校验。"""
    for name in PIPELINE_ORDER:
        description, check_entries = STAGES[name]
        entries = check_entries if check else (GENERATE_ENTRY,)
        print(f"[codegen] {name.value}: {description}")
        module = importlib.import_module(name.value)
        for entry in entries:
            rc = _invoke(module, entry, repo_root)
            if rc != int(ExitCode.SUCCESS):
                print(f"[codegen] {name.value}.{entry} FAILED (rc={rc})", file=sys.stderr)
                return rc
    action = "verified" if check else "generated"
    print(f"[codegen] all {len(PIPELINE_ORDER)} generators {action} successfully")
    return int(ExitCode.SUCCESS)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：默认生成全部产物，--check 校验零漂移。"""
    parser = argparse.ArgumentParser(
        description="AirymaxOS codegen 管道统一入口（06-codegen-pipeline.md §6.3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  %(prog)s                 # 按依赖序生成全部产物（默认仓库根）
  %(prog)s --check         # CI 零漂移校验（含 syscall_64.tbl Airymax 段）
  %(prog)s --repo-root DIR # 指定 agent-linux 仓库根
""",
    )
    parser.add_argument(
        "--repo-root",
        default=str(default_repo_root()),
        help="agent-linux 仓库根目录（默认: %(default)s）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="校验模式：对比生成内容与已提交文件，漂移返回非零退出码",
    )
    args = parser.parse_args(argv)
    return run_pipeline(Path(args.repo_root).resolve(), check=args.check)


if __name__ == "__main__":
    sys.exit(main())