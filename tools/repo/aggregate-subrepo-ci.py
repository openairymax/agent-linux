#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
aggregate-subrepo-ci.py — 聚合叶子子仓的 CI 状态

被 mgmt-verify.yml 引用。读取 ``git submodule status``，并结合
``.gitmodules`` 派生的子模块清单，对每个子仓调用 GitHub API 查询其 HEAD
commit 的 check runs / combined status，输出聚合报告。

退出码：
    0 — 无 token（设计期降级）、全部绿，或仅有 unknown（告警不阻断）
    1 — --fail-on-red 且存在红（red）子仓
    2 — 参数或本地命令错误

说明：叶子子仓尚未全部接入 CI 时其状态为 unknown，属预期情况，
不阻断管理仓 CI；仅当明确出现 red 时才以 --fail-on-red 阻断。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


# ─── 子仓配置 ────────────────────────────────────────────────────────
# .gitmodules 中 url 为相对路径（../kernel.git），无法直接得到 owner。
# 此处提供默认映射，可用环境变量 GITHUB_OWNER 覆盖 owner。
# 子模块清单不硬编码，改由 .gitmodules 派生（见 list_submodule_paths）。
DEFAULT_OWNER = os.environ.get("GITHUB_OWNER", "openairymax")


def list_submodule_paths(root):
    """从 root/.gitmodules 派生声明的子模块路径清单。

    返回 (paths, 错误信息)。错误信息为 None 时 paths 有效；
    否则 paths 为空列表并携带原因。
    """
    gitmodules = os.path.join(root, ".gitmodules")
    if not os.path.isfile(gitmodules):
        return [], ".gitmodules 不存在"
    try:
        proc = subprocess.run(
            ["git", "config", "-f", gitmodules, "--get-regexp",
             r"^submodule\..*\.path$"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [], f"git 不可用或超时：{exc}"
    if proc.returncode not in (0, 1):
        return [], proc.stderr.strip() or "git config 读取失败"
    paths = []
    for line in proc.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and fields[1].strip():
            paths.append(fields[1].strip())
    return paths, None


def http_request(method, url, token, body=None):
    """发起 GitHub API 请求，返回 (status, json_or_text)。"""
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-linux-ci",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"message": raw}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"message": f"网络错误：{exc}"}


def parse_submodule_status(root):
    """运行 ``git submodule status``，返回 [(path, sha, flags), ...]。

    flags 包含前缀符号：' ' 已检出，'-' 未初始化，'+' 不同步，'U' 冲突。
    """
    try:
        proc = subprocess.run(
            ["git", "submodule", "status"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"git 不可用或超时：{exc}"
    if proc.returncode != 0:
        return None, proc.stderr.strip() or "git submodule status 失败"

    # 行格式： <sign><sha> <path> (<describe>)
    line_re = re.compile(
        r"^(?P<sign>[ +-U])(?P<sha>[0-9a-f]{7,40})\s+(?P<path>\S+)"
    )
    subs = []
    for line in proc.stdout.splitlines():
        m = line_re.match(line)
        if m:
            subs.append((m.group("path"), m.group("sha"), m.group("sign")))
    return subs, None


def fetch_ci_state(api, owner_repo, sha, token):
    """获取 commit 的 check runs + combined status，返回 state 字符串。"""
    fail_conclusions = {
        "failure", "cancelled", "timed_out", "action_required",
    }
    # check runs
    url = f"{api}/repos/{owner_repo}/commits/{sha}/check-runs?per_page=100"
    status, payload = http_request("GET", url, token)
    if status == 404:
        return "unknown", "repo/commit 未找到"
    if status != 200 or not isinstance(payload, dict):
        return "unknown", f"check-runs HTTP {status}"
    runs = payload.get("check_runs", []) or []
    for run in runs:
        st = run.get("status")
        con = run.get("conclusion")
        if st in ("queued", "in_progress", "waiting", "pending"):
            return "pending", f"{len(runs)} check runs（进行中）"
        if st == "completed" and con in fail_conclusions:
            return "red", f"check run {run.get('name')!r} conclusion={con}"

    # combined status
    url = f"{api}/repos/{owner_repo}/commits/{sha}/status"
    status, payload = http_request("GET", url, token)
    combined = None
    if status == 200 and isinstance(payload, dict):
        combined = payload.get("state")
    if combined in ("failure", "error"):
        return "red", f"combined status={combined}"
    if combined == "pending":
        return "pending", "combined status=pending"

    if not runs and combined in (None, "no status"):
        return "unknown", "无 check runs / status"
    return "green", f"{len(runs)} check runs，combined={combined}"


def main():
    parser = argparse.ArgumentParser(
        description="聚合叶子子仓的 CI 状态"
    )
    parser.add_argument(
        "--fail-on-red", action="store_true",
        help="存在红（red）子仓时以退出码 1 终止；unknown 仅告警",
    )
    parser.add_argument(
        "--root", default=".",
        help="git 仓库根目录（默认当前目录）",
    )
    parser.add_argument(
        "--token", default=os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub token（默认读 $GITHUB_TOKEN）",
    )
    parser.add_argument(
        "--owner", default=DEFAULT_OWNER,
        help=f"GitHub owner（默认 {DEFAULT_OWNER}）",
    )
    parser.add_argument(
        "--api", default="https://api.github.com",
        help="GitHub API 根（默认 https://api.github.com）",
    )
    args = parser.parse_args()

    if not args.token.strip():
        print("SKIP: no token, design phase")
        return 0

    subs, err = parse_submodule_status(args.root)
    if err is not None:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    expected, gm_err = list_submodule_paths(args.root)
    if gm_err is not None:
        print(
            f"WARN: 无法从 .gitmodules 派生清单（{gm_err}），"
            "仅报告 git submodule status 中的子模块",
            file=sys.stderr,
        )

    # 子模块缺失时也输出明确报告
    found_paths = {s[0] for s in subs}
    for name in expected:
        if name not in found_paths:
            subs.append((name, "?", "-"))

    print(f"聚合 {len(subs)} 个子仓的 CI 状态（owner={args.owner}）：")
    print("-" * 72)
    red_count = 0
    unknown_count = 0
    for path, sha, sign in subs:
        repo = f"{args.owner}/{path}"
        owner_repo = urllib.parse.quote(repo, safe="/")
        if sha == "?":
            state, detail = "unknown", "子仓未在 submodule status 中"
        else:
            state, detail = fetch_ci_state(
                args.api, owner_repo, sha, args.token
            )
        print(f"  {path:<14} {sha[:12]:<12} {state:<8} {detail}")
        if state == "red":
            red_count += 1
        elif state == "unknown":
            unknown_count += 1

    print("-" * 72)
    print(f"汇总：red={red_count}，unknown={unknown_count}，"
          f"total={len(subs)}")

    if unknown_count > 0:
        print(
            f"WARN: {unknown_count} 个子仓 CI 状态未知"
            "（叶子仓未接入 CI 时属预期）",
            file=sys.stderr,
        )
    if args.fail_on_red and red_count > 0:
        print("FAIL: 存在红（red）子仓（--fail-on-red 已启用）", file=sys.stderr)
        return 1
    print("OK: 聚合完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
