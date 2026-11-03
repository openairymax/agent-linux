# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# test_codeowners.py — tools/codeowners.py 的投影契约
#
# 守护三件事：仓库内 CODEOWNERS 与 MAINTAINERS 无漂移、缺 O: 的块被拒绝
# （而不是生成无人认领的条目）、投影顺序与所有者逐行可预测。
# ===========================================================================

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "codeowners", ROOT / "tools" / "codeowners.py")
CODEOWNERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CODEOWNERS)

PREAMBLE = "List of maintainers\n===================\n\n" + "-" * 70 + "\n"


def feed(tmp_path, body):
    """写一份临时 MAINTAINERS 并返回投影文本。"""
    source = tmp_path / "MAINTAINERS"
    source.write_text(PREAMBLE + body, encoding="utf-8")
    return CODEOWNERS.render(CODEOWNERS.parse_blocks(
        CODEOWNERS.after_preamble(CODEOWNERS.read_lines(source))))


class TestCodeowners:
    def test_repository_is_in_sync(self):
        assert CODEOWNERS.main(["--check"]) == 0

    def test_missing_owner_is_rejected(self, tmp_path):
        with pytest.raises(CODEOWNERS.FeedError):
            feed(tmp_path, "GHOST SUBSYSTEM\nF:\tghost/\n")

    def test_projection_keeps_order(self, tmp_path):
        text = feed(tmp_path,
                    "UMBRELLA\nO:\t@spharx\nF:\t*\n\n"
                    "KERNEL SUBSYSTEM\nO:\t@kernel-lead\nF:\tkernel/\n")
        rows = [line.split() for line in text.splitlines()
                if line and not line.startswith("#")]
        assert rows == [["*", "@spharx"], ["kernel/", "@kernel-lead"]]
