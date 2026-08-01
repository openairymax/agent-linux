#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
aggregate-subrepo-ci.py — 聚合 8 个子仓的 CI 状态

被 mgmt-orchestrator.yml 引用。读取 ``git submodule status``，
对每个子仓调用 GitHub API 查询其 HEAD commit 的 check runs / combined
status，输出聚合报告。

退出码：
    0 — 全部子仓 CI 绿，或无 token（design phase 降级）
    1 — --fail-on-red 且存在红/未知子仓
    2 — 参数或本地命令错误
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
DEFAULT_OWNER = os.environ.get("GITHUB_OWNER", "openairymax")
EXPECTED_SUBMODULES = [
    "kernel", "memory", "security", "cognition",
    "services", "system", "cloudnative", "tests-linux",
]


def http_request(method, url, token, body=None):
    """发起 GitHub API 请求，返回 (status, json_or_text)。"""
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agentrt-linux-ci",
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
        description="聚合 8 个子仓的 CI 状态"
    )
    parser.add_argument(
        "--fail-on-red", action="store_true",
        help="存在红/未知子仓时以退出码 1 终止",
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

    # 子模块缺失时也输出明确报告
    found_paths = {s[0] for s in subs}
    for name in EXPECTED_SUBMODULES:
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

    if args.fail_on_red and (red_count > 0 or unknown_count > 0):
        print("FAIL: 存在红/未知子仓（--fail-on-red 已启用）", file=sys.stderr)
        return 1
    print("OK: 聚合完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
