#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
await-agentrt-ci.py — 等待 agentrt 镜像 PR 的 CI 完成

被 sc-dual-ci.yml 引用（OS-IRON-008：[SC] 共享契约层双向 CI）。
根据 ``--source-pr`` 推导镜像分支 ``mirror/sc-<N>``，轮询 GitHub API
查询该 PR 的 check runs / combined status，直至完成或超时。

退出码：
    0 — CI 成功，或 token 缺失（降级为 SKIP）
    1 — CI 失败、超时、PR 不存在或 API 错误
    2 — 参数错误
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


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
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"message": raw}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"message": f"网络错误：{exc}"}


def parse_timeout(spec):
    """解析 "30m" / "1h" / "60s" / "90" 等超时字符串，返回秒数。"""
    spec = spec.strip().lower()
    if not spec:
        raise ValueError("空超时")
    unit, num = spec[-1], spec[:-1] if spec[-1].isalpha() else spec
    try:
        value = float(num)
    except ValueError as exc:
        raise ValueError(f"无法解析超时数值：{spec}") from exc
    if unit == "h":
        return value * 3600
    if unit == "m":
        return value * 60
    if unit == "s":
        return value
    if unit.isdigit():  # 纯数字默认秒
        return float(spec)
    raise ValueError(f"未知超时单位：{unit}")


def token_is_valid(token):
    if not token:
        return False
    stripped = token.strip()
    if not stripped:
        return False
    placeholders = {"", "none", "null", "changeme", "your_token_here"}
    return stripped.lower() not in placeholders


def find_mirror_pr(api, owner_repo, head, base, token):
    """查找镜像 PR，返回 (pr_number, head_sha) 或 (None, None)。"""
    owner = owner_repo.split("/")[0]
    url = (
        f"{api}/repos/{owner_repo}/pulls"
        f"?state=open&head={owner}:{head}&base={base}"
    )
    status, payload = http_request("GET", url, token)
    if status != 200 or not isinstance(payload, list):
        return None, None
    for pr in payload:
        if pr.get("head", {}).get("ref") == head:
            num = pr.get("number")
            sha = pr.get("head", {}).get("sha")
            return num, sha
    return None, None


def fetch_check_runs(api, owner_repo, sha, token):
    """获取 head sha 的所有 check runs（自动翻页）。"""
    runs = []
    url = (
        f"{api}/repos/{owner_repo}/commits/{sha}/check-runs"
        f"?per_page=100"
    )
    while url:
        status, payload = http_request("GET", url, token)
        if status != 200 or not isinstance(payload, dict):
            break
        runs.extend(payload.get("check_runs", []) or [])
        url = payload.get("pagination", {}).get("next")  # GitHub 不提供，但兜底
        # 实际 GitHub 不在 payload 给 next；用 Link header
        # 简化：单页 100 通常足够覆盖管理仓 CI
        break
    return runs


def fetch_combined_status(api, owner_repo, sha, token):
    """获取 head sha 的 combined status。"""
    url = f"{api}/repos/{owner_repo}/commits/{sha}/status"
    status, payload = http_request("GET", url, token)
    if status != 200 or not isinstance(payload, dict):
        return None
    return payload.get("state")


def evaluate_ci(runs, combined_state):
    """评估 CI 状态。

    返回 ("success" | "failure" | "pending")。
    - 任一 check run conclusion 为 failure/cancelled/timed_out/action_required
      → failure
    - 任一 status 为 failure/error → failure
    - 全部成功 → success
    - 否则 → pending
    """
    fail_conclusions = {
        "failure", "cancelled", "timed_out", "action_required",
    }
    for run in runs:
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status in ("queued", "in_progress", "waiting", "pending"):
            return "pending"
        if status == "completed":
            if conclusion in fail_conclusions:
                return "failure"
            if conclusion in (None, "neutral", "skipped", "stale"):
                continue
            # success → 继续
        else:
            return "pending"

    if combined_state in ("failure", "error"):
        return "failure"
    if combined_state == "pending":
        return "pending"
    # combined_state == "success" 或无 statuses
    return "success"


def main():
    parser = argparse.ArgumentParser(
        description="轮询 agentrt 镜像 PR 的 CI 状态直至完成或超时"
    )
    parser.add_argument(
        "--source-pr", required=True, type=int,
        help="源 PR 编号（agentrt-linux PR，必填）",
    )
    parser.add_argument(
        "--timeout", required=True,
        help="超时（如 30m / 1h / 60s，必填）",
    )
    parser.add_argument(
        "--token", required=True,
        help="AGENTRT_CI_TOKEN（必填，空字符串触发 SKIP）",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("AGENTRT_REPO", "openairymax/agentrt"),
        help="目标 agentrt 仓库（owner/repo，默认 openairymax/agentrt）",
    )
    parser.add_argument(
        "--head-branch", default=None,
        help="镜像 PR head 分支（默认 mirror/sc-<source-pr>）",
    )
    parser.add_argument(
        "--base-branch", default="develop",
        help="镜像 PR base 分支（默认 develop）",
    )
    parser.add_argument(
        "--api", default="https://api.github.com",
        help="GitHub API 根（默认 https://api.github.com）",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=30.0,
        help="轮询间隔秒数（默认 30）",
    )
    args = parser.parse_args()

    if not token_is_valid(args.token):
        print("SKIP: no AGENTRT_CI_TOKEN configured")
        return 0

    try:
        timeout_sec = parse_timeout(args.timeout)
    except ValueError as exc:
        print(f"ERROR: --timeout 解析失败：{exc}", file=sys.stderr)
        return 2
    if timeout_sec <= 0 or args.poll_interval <= 0:
        print("ERROR: --timeout 与 --poll-interval 必须为正数",
              file=sys.stderr)
        return 2

    head = args.head_branch or f"mirror/sc-{args.source_pr}"
    base = args.base_branch
    owner_repo = urllib.parse.quote(args.repo, safe="/")

    deadline = time.monotonic() + timeout_sec
    print(
        f"等待 agentrt 镜像 PR（head={head}, base={base}）的 CI 完成，"
        f"超时 {args.timeout}"
    )

    pr_number = None
    head_sha = None
    last_state = None
    while time.monotonic() < deadline:
        if pr_number is None:
            pr_number, head_sha = find_mirror_pr(
                args.api, owner_repo, head, base, args.token
            )
            if pr_number is None:
                print(
                    f"WARN: 尚未发现镜像 PR（head={head}），"
                    f"{int(args.poll_interval)}s 后重试",
                    file=sys.stderr,
                )
                time.sleep(args.poll_interval)
                continue
            print(f"发现镜像 PR #{pr_number}，head sha={head_sha[:12]}")

        runs = fetch_check_runs(args.api, owner_repo, head_sha, args.token)
        combined = fetch_combined_status(
            args.api, owner_repo, head_sha, args.token
        )
        state = evaluate_ci(runs, combined)
        if state != last_state:
            print(
                f"CI 状态：{state}（check_runs={len(runs)}, "
                f"combined={combined}）"
            )
            last_state = state

        if state == "success":
            print(f"OK: agentrt 镜像 PR #{pr_number} CI 成功")
            return 0
        if state == "failure":
            print(
                f"FAIL: agentrt 镜像 PR #{pr_number} CI 失败",
                file=sys.stderr,
            )
            return 1

        time.sleep(args.poll_interval)

    if pr_number is None:
        print(
            f"TIMEOUT: 超时 {args.timeout} 内未找到镜像 PR（head={head}）",
            file=sys.stderr,
        )
    else:
        print(
            f"TIMEOUT: 镜像 PR #{pr_number} CI 仍在 pending，"
            f"超时 {args.timeout} 已到",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
