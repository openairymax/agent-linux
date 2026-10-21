# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""pick_image.py 单元测试。

覆盖：三条认领判据的正负用例（制品名、过期状态、提交 SHA）、同提交多命中的
择新、过期制品不可认领、验收镜像不被认领、列表两种 JSON 形态、CLI 退出码
（0 命中 / 1 未命中 / 2 输入非法）与 `$GITHUB_OUTPUT` 写入。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pick_image as pk

SHA = "ca0ac20" + "0" * 33
OTHER = "59be538" + "0" * 33


def art(name: str = "airymaxos-image", sha: str = SHA, run: int = 7,
        ident: int = 1, expired: bool = False, created: str = "2026-10-21T05:00:00Z"):
    """构造一条 GitHub 制品条目。"""
    return {
        "id": ident,
        "name": name,
        "expired": expired,
        "created_at": created,
        "workflow_run": {"id": run, "head_sha": sha},
    }


def write(tmp_path: Path, payload: object) -> Path:
    """把 payload 写成 JSON 文件并返回路径。"""
    path = tmp_path / "artifacts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pick_matching_head_sha():
    """名称、提交、未过期三条件同时成立时命中。"""
    item = pk.pick([art()], "airymaxos-image", SHA)
    assert item["workflow_run"]["id"] == 7


def test_pick_skips_acceptance_artifact():
    """B 轮验收镜像制品名不同，永不参与发行物认领。"""
    items = [art(name="airymaxos-image-acceptance")]
    with pytest.raises(pk.MissError):
        pk.pick(items, "airymaxos-image", SHA)


def test_pick_skips_expired():
    """过期制品不可下载，不得认领。"""
    with pytest.raises(pk.MissError):
        pk.pick([art(expired=True)], "airymaxos-image", SHA)


def test_pick_skips_other_commit():
    """其他提交的镜像即使同名也不可用。"""
    with pytest.raises(pk.MissError):
        pk.pick([art(sha=OTHER)], "airymaxos-image", SHA)


def test_pick_newest_wins():
    """同一提交多次组装时取 created_at 最新者。"""
    items = [art(ident=1, run=11, created="2026-10-20T00:00:00Z"),
             art(ident=2, run=12, created="2026-10-21T00:00:00Z")]
    assert pk.pick(items, "airymaxos-image", SHA)["workflow_run"]["id"] == 12


def test_read_list_accepts_both_shapes(tmp_path: Path):
    """兼容 REST 响应体与裸数组两种列表形态。"""
    wrapped = pk.read_list(write(tmp_path, {"total_count": 1, "artifacts": [art()]}))
    bare = pk.read_list(write(tmp_path, [art()]))
    assert wrapped == bare


def test_read_list_rejects_bad_json(tmp_path: Path):
    """非法 JSON 归类为输入错误。"""
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(pk.ToolError):
        pk.read_list(path)


def test_cli_miss_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """未命中返回 1，并给出可操作的失败说明。"""
    code = pk.main(["--artifacts", str(write(tmp_path, [])), "--sha", SHA])
    assert code == pk.EXIT_MISS
    assert "image-ci" in capsys.readouterr().err


def test_cli_usage_exit_code(tmp_path: Path):
    """非法输入返回 2。"""
    path = tmp_path / "doc.json"
    path.write_text('"nope"', encoding="utf-8")
    assert pk.main(["--artifacts", str(path), "--sha", SHA]) == pk.EXIT_USAGE


def test_cli_emit_output_file(tmp_path: Path):
    """命中时把 run_id 与 artifact 追加写入 $GITHUB_OUTPUT。"""
    out = tmp_path / "gh_output"
    code = pk.main(["--artifacts", str(write(tmp_path, {"artifacts": [art(run=42)]})),
                    "--sha", SHA, "--output", str(out)])
    assert code == pk.EXIT_OK
    assert out.read_text(encoding="utf-8") == \
        "run_id=42\nartifact=airymaxos-image\n"
