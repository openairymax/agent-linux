#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
create-agentrt-mirror-pr.py — 在 agentrt 仓创建镜像 PR

被 sc-dual-ci.yml 引用（OS-IRON-008：[SC] 共享契约层双向 CI）。
读取 ``--patches`` 目录下的 .patch 文件作为 PR 描述，调用 GitHub
API 在 agentrt 仓创建镜像 PR，输出 PR URL。

注意：本脚本假设镜像分支 ``mirror/sc-<N>`` 已由其他自动化推送就绪
（CI 中由上游 push 步骤完成）。脚本只负责通过 token 创建 PR。

退出码：
    0 — PR 创建成功 / 已存在 / token 缺失（降级为 SKIP）
    1 — API 调用失败（token 无效、分支不存在、网络错误等）
    2 — 参数或本地文件错误
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def http_request(method, url, token, body=None):
    """发起 GitHub API 请求，返回 (status, json_or_text)。

    body 为 dict 时序列化为 JSON；为 None 时不发 body。
    """
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


def list_patch_files(patches_dir):
    """列出 patches 目录下所有 .patch 文件，按文件名排序。"""
    root = Path(patches_dir)
    if not root.is_dir():
        return None, f"--patches 目录不存在：{patches_dir}"
    patches = sorted(p.name for p in root.glob("*.patch"))
    return patches, None


def token_is_valid(token):
    """非空且不含明显占位符即视为可用。"""
    if not token:
        return False
    stripped = token.strip()
    if not stripped:
        return False
    placeholders = {"", "none", "null", "changeme", "your_token_here"}
    return stripped.lower() not in placeholders


def main():
    parser = argparse.ArgumentParser(
        description="在 agentrt 仓创建 [SC] 镜像 PR"
    )
    parser.add_argument(
        "--patches",
        required=True,
        help="git format-patch 输出目录（必填）",
    )
    parser.add_argument(
        "--source-pr",
        required=True,
        type=int,
        help="源 PR 编号（agentrt-linux PR，必填）",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="AGENTRT_CI_TOKEN（必填，空字符串触发 SKIP）",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("AGENTRT_REPO", "openairymax/agentrt"),
        help="目标 agentrt 仓库（owner/repo，默认 openairymax/agentrt）",
    )
    parser.add_argument(
        "--head-branch",
        default=None,
        help="镜像 PR head 分支（默认 mirror/sc-<source-pr>）",
    )
    parser.add_argument(
        "--base-branch",
        default="develop",
        help="镜像 PR base 分支（默认 develop）",
    )
    parser.add_argument(
        "--api",
        default="https://api.github.com",
        help="GitHub API 根（默认 https://api.github.com）",
    )
    args = parser.parse_args()

    # token 缺失：SKIP（允许 CI 继续）
    if not token_is_valid(args.token):
        print("SKIP: no AGENTRT_CI_TOKEN configured")
        return 0

    patches, err = list_patch_files(args.patches)
    if err is not None:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    if not patches:
        print("WARN: --patches 目录下没有 .patch 文件，仍尝试创建 PR",
              file=sys.stderr)

    head = args.head_branch or f"mirror/sc-{args.source_pr}"
    base = args.base_branch
    owner_repo = urllib.parse.quote(args.repo, safe="/")

    # 列出已有 PR，若同 head/base 已存在则复用
    list_url = (
        f"{args.api}/repos/{owner_repo}/pulls"
        f"?state=open&head={urllib.parse.quote(args.repo.split('/')[0])}:{head}"
        f"&base={base}"
    )
    status, payload = http_request("GET", list_url, args.token)
    if status == 200 and isinstance(payload, list):
        for pr in payload:
            if pr.get("head", {}).get("ref") == head:
                url = pr.get("html_url")
                print(f"SKIP: mirror PR 已存在：{url}")
                return 0
    # 422/404 等忽略，继续尝试创建

    title = f"[SC] mirror of agentrt-linux #{args.source_pr}"
    body_lines = [
        f"本 PR 由 agentrt-linux `sc-dual-ci` 工作流自动创建。",
        "",
        f"- 源 PR：agentrt-linux #{args.source_pr}",
        f"- head 分支：`{head}`",
        f"- base 分支：`{base}`",
        "",
        "包含的补丁文件：",
    ]
    if patches:
        for name in patches:
            body_lines.append(f"  - `{name}`")
    else:
        body_lines.append("  - （无）")
    body = "\n".join(body_lines)

    create_url = f"{args.api}/repos/{owner_repo}/pulls"
    status, payload = http_request(
        "POST", create_url, args.token,
        body={"title": title, "head": head, "base": base, "body": body},
    )
    if status == 201 and isinstance(payload, dict):
        url = payload.get("html_url")
        number = payload.get("number")
        print(f"OK: 镜像 PR #{number} 已创建：{url}")
        return 0

    msg = "?"
    if isinstance(payload, dict):
        msg = payload.get("message", "?")
        errors = payload.get("errors")
        if isinstance(errors, list):
            msg += " / " + "; ".join(
                str(e.get("message", e)) for e in errors if isinstance(e, dict)
            )
    print(
        f"ERROR: 创建镜像 PR 失败（HTTP {status}）：{msg}",
        file=sys.stderr,
    )
    print(
        "HINT: 请确认 head 分支已推送到 agentrt 仓且 token 有 repo 权限",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
