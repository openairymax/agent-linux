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
# 子模块清单不硬编码，改由 .gitmodules 派生（见 sub_paths）。
DEFAULT_OWNER = os.environ.get("GITHUB_OWNER", "openairymax")


def _gm_read(gitmodules):
    """读 .gitmodules 的 path 行；返回 (stdout, 错误信息)。"""
    try:
        proc = subprocess.run(
            ["git", "config", "-f", gitmodules, "--get-regexp",
             r"^submodule\..*\.path$"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"git 不可用或超时：{exc}"
    if proc.returncode not in (0, 1):
        return None, proc.stderr.strip() or "git config 读取失败"
    return proc.stdout, None


def _path_of(line):
    """`submodule.X.path <path>` → path；字段不足或路径为空时返回 None。"""
    fields = line.split(None, 1)
    if len(fields) == 2 and fields[1].strip():
        return fields[1].strip()
    return None


def _path_lines(stdout):
    """逐行取声明的子模块路径，非路径行丢弃。"""
    return [path for path in map(_path_of, stdout.splitlines()) if path]


def _gm_paths(gitmodules):
    """从 .gitmodules 提取声明的子模块路径。"""
    stdout, err = _gm_read(gitmodules)
    if err is not None:
        return [], err
    return _path_lines(stdout), None


def sub_paths(root):
    """从 root/.gitmodules 派生声明的子模块路径清单。

    返回 (paths, 错误信息)。错误信息为 None 时 paths 有效；
    否则 paths 为空列表并携带原因。
    """
    gitmodules = os.path.join(root, ".gitmodules")
    if not os.path.isfile(gitmodules):
        return [], ".gitmodules 不存在"
    return _gm_paths(gitmodules)


def _headers(token, body):
    """请求头：有 token 才带鉴权，有 body 才声明 JSON。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-linux-ci",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    return headers


def _parse(raw):
    """响应体按 JSON 解析；不是 JSON 时返回 None，由调用方决定语义。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _body_bytes(body):
    """请求体序列化为字节；无 body 时为 None。"""
    return None if body is None else json.dumps(body).encode("utf-8")


def _http_ok(req):
    """正常响应：返回 (状态码, 解析结果)。"""
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, _parse(raw)


def _http_error(exc):
    """HTTPError：能解析为 JSON 就用 JSON，否则回落到 message 文本。"""
    raw = exc.read().decode("utf-8", errors="replace")
    parsed = _parse(raw)
    if parsed is None:
        return exc.code, {"message": raw}
    return exc.code, parsed


def http_request(method, url, token, body=None):
    """发起 GitHub API 请求，返回 (status, json_or_text)。"""
    req = urllib.request.Request(
        url, data=_body_bytes(body), headers=_headers(token, body),
        method=method
    )
    try:
        return _http_ok(req)
    except urllib.error.HTTPError as exc:
        return _http_error(exc)
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"message": f"网络错误：{exc}"}


def parse_sub_stat(root):
    """运行 ``git submodule status``，返回 [(path, sha, flags), ...]。

    flags 包含前缀符号：' ' 已检出，'-' 未初始化，'+' 不同步，'U' 冲突。
    """
    out, err = _sub_status(root)
    if err is not None:
        return None, err
    return _sub_rows(out), None


def _sub_status(root):
    """运行 ``git submodule status``；失败时返回错误描述。"""
    try:
        proc = subprocess.run(
            ["git", "submodule", "status"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "", f"git 不可用或超时：{exc}"
    if proc.returncode != 0:
        return "", proc.stderr.strip() or "git submodule status 失败"
    return proc.stdout, None


def _sub_rows(out):
    """逐行解析 ``<sign><sha> <path>``，忽略不匹配的行。"""
    # 行格式： <sign><sha> <path> (<describe>)
    line_re = re.compile(
        r"^(?P<sign>[ +-U])(?P<sha>[0-9a-f]{7,40})\s+(?P<path>\S+)"
    )
    subs = []
    for line in out.splitlines():
        m = line_re.match(line)
        if m:
            subs.append((m.group("path"), m.group("sha"), m.group("sign")))
    return subs


def fetch_ci_state(api, owner_repo, sha, token):
    """获取 commit 的 check runs + combined status，返回 state 字符串。"""
    runs, verdict = _ci_runs(api, owner_repo, sha, token)
    if verdict is not None:
        return verdict
    return _ci_status(api, owner_repo, sha, token, len(runs))


def _runs_hit(runs):
    """首条给出定论的 check run → (state, detail)；全部无定论时 None。"""
    failed = {"failure", "cancelled", "timed_out", "action_required"}
    pending = {"queued", "in_progress", "waiting", "pending"}
    for run in runs:
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status in pending:
            return "pending", f"{len(runs)} check runs（进行中）"
        if status == "completed" and conclusion in failed:
            return "red", f"check run {run.get('name')!r} conclusion={conclusion}"
    return None


def _ci_runs(api, owner_repo, sha, token):
    """check runs 的判定：返回 (runs, verdict)，无定论时 verdict 为 None。"""
    url = f"{api}/repos/{owner_repo}/commits/{sha}/check-runs?per_page=100"
    status, payload = http_request("GET", url, token)
    if status == 404:
        return [], ("unknown", "repo/commit 未找到")
    if status != 200 or not isinstance(payload, dict):
        return [], ("unknown", f"check-runs HTTP {status}")
    runs = payload.get("check_runs", []) or []
    return runs, _runs_hit(runs)


def _combined(api, owner_repo, sha, token):
    """combined status 的 state；接口不可用时返回 None。"""
    url = f"{api}/repos/{owner_repo}/commits/{sha}/status"
    status, payload = http_request("GET", url, token)
    if status == 200 and isinstance(payload, dict):
        return payload.get("state")
    return None


def _ci_status(api, owner_repo, sha, token, run_count):
    """combined status 的判定：check runs 无定论时以它收口。"""
    combined = _combined(api, owner_repo, sha, token)
    if combined in ("failure", "error"):
        return "red", f"combined status={combined}"
    if combined == "pending":
        return "pending", "combined status=pending"
    if not run_count and combined in (None, "no status"):
        return "unknown", "无 check runs / status"
    return "green", f"{run_count} check runs，combined={combined}"


def _build_parser():
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
    return parser


def _expect(root):
    """.gitmodules 清单只用来补缺，读不出来就退回 status 里的条目。"""
    expected, gm_err = sub_paths(root)
    if gm_err is not None:
        print(
            f"WARN: 无法从 .gitmodules 派生清单（{gm_err}），"
            "仅报告 git submodule status 中的子模块",
            file=sys.stderr,
        )
    return expected


def _seed(subs, expected):
    """子模块缺失时也输出明确报告：以 ? 占位，让缺口出现在聚合表里。"""
    found = {s[0] for s in subs}
    return subs + [(n, "?", "-") for n in expected if n not in found]


def _row(args, path, sha):
    """一行的状态。不在 status 里的子仓没有 sha，连请求都不发。"""
    if sha == "?":
        return "unknown", "子仓未在 submodule status 中"
    repo = f"{args.owner}/{path}"
    owner_repo = urllib.parse.quote(repo, safe="/")
    return fetch_ci_state(args.api, owner_repo, sha, args.token)


def _table(subs, args):
    """打印聚合表，返回 (red, unknown) 计数。"""
    red = unknown = 0
    for path, sha, _sign in subs:
        state, detail = _row(args, path, sha)
        print(f"  {path:<14} {sha[:12]:<12} {state:<8} {detail}")
        if state == "red":
            red += 1
        elif state == "unknown":
            unknown += 1
    return red, unknown


def _verdict(args, red, unknown):
    """红子仓只在 --fail-on-red 下拦下；unknown 一律只告警。"""
    if unknown > 0:
        print(
            f"WARN: {unknown} 个子仓 CI 状态未知"
            "（叶子仓未接入 CI 时属预期）",
            file=sys.stderr,
        )
    if args.fail_on_red and red > 0:
        print("FAIL: 存在红（red）子仓（--fail-on-red 已启用）", file=sys.stderr)
        return 1
    print("OK: 聚合完成")
    return 0


def main():
    args = _build_parser().parse_args()

    if not args.token.strip():
        print("SKIP: no token, design phase")
        return 0

    subs, err = parse_sub_stat(args.root)
    if err is not None:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    subs = _seed(subs, _expect(args.root))
    print(f"聚合 {len(subs)} 个子仓的 CI 状态（owner={args.owner}）：")
    print("-" * 72)
    red, unknown = _table(subs, args)
    print("-" * 72)
    print(f"汇总：red={red}，unknown={unknown}，total={len(subs)}")
    return _verdict(args, red, unknown)


if __name__ == "__main__":
    sys.exit(main())
