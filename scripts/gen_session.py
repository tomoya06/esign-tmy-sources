#!/usr/bin/env python3
"""本地生成 Telethon StringSession（用于 GitHub Secrets 的 TG_SESSION）。

用法:
    python scripts/gen_session.py
按提示输入 api_id / api_hash，完成手机号 + 验证码登录后打印 StringSession。

代理: Telethon 走 MTProto 原始连接，不读 https_proxy 环境变量。
仅本地运行需要：无法直连 Telegram 时设置代理环境变量（按优先级 TG_PROXY > all_proxy > https_proxy），
本脚本自动解析并传给 TelegramClient，例如:
    export all_proxy=socks5://127.0.0.1:7890
GitHub Actions 内直连 Telegram，无需也不使用本地代理。
代理支持需要 pip install 'telethon[socks]'。

警告: StringSession 等同账号凭据，严禁提交仓库或泄露。
"""

import asyncio
import os
import re

from telethon import TelegramClient
from telethon.sessions import StringSession


def proxy_from_env():
    raw = (os.environ.get("TG_PROXY") or os.environ.get("all_proxy") or os.environ.get("https_proxy") or "").strip()
    if not raw:
        return None
    m = re.match(r"(?:(\w+)://)?([^:/@\s]+):(\d+)$", raw)
    if not m:
        print(f"忽略无法解析的代理配置: {raw}")
        return None
    scheme = (m.group(1) or "socks5").lower()
    try:
        import socks
    except ImportError:
        raise SystemExit("使用代理需要安装 socks 支持: pip install 'telethon[socks]'")
    ptype = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}.get(scheme)
    if ptype is None:
        print(f"不支持的代理协议: {scheme}")
        return None
    return (ptype, m.group(2), int(m.group(3)))


async def main():
    api_id = int(input("api_id: ").strip())
    api_hash = input("api_hash: ").strip()
    proxy = proxy_from_env()
    if proxy:
        print(f"使用代理: {proxy[1]}:{proxy[2]}")
    async with TelegramClient(StringSession(), api_id, api_hash, proxy=proxy) as client:
        me = await client.get_me()
        print(f"\n登录成功: {me.first_name} (@{me.username})")
        print("以下 StringSession 存入 GitHub Secrets 的 TG_SESSION:\n")
        print(client.session.save())
        print("\n注意: 严禁提交进仓库或泄露给他人。")


if __name__ == "__main__":
    asyncio.run(main())
