#!/usr/bin/env python3
"""本地生成 Telethon StringSession（用于 GitHub Secrets 的 TG_SESSION）。

用法:
    python scripts/gen_session.py
按提示输入 api_id / api_hash，完成手机号 + 验证码登录后打印 StringSession。
警告: StringSession 等同账号凭据，严禁提交仓库或泄露。
"""

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id = int(input("api_id: ").strip())
    api_hash = input("api_hash: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        print(f"\n登录成功: {me.first_name} (@{me.username})")
        print("以下 StringSession 存入 GitHub Secrets 的 TG_SESSION:\n")
        print(client.session.save())
        print("\n注意: 严禁提交进仓库或泄露给他人。")


if __name__ == "__main__":
    asyncio.run(main())
