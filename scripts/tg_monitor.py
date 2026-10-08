#!/usr/bin/env python3
"""Telegram 公开频道 .ipa 抓取模块（Telethon + 用户 StringSession）。

运行环境: GitHub Actions 或可直连 Telegram 的网络，不使用任何代理；
本地登录生成 session 请用 gen_session.py（仅它需要代理）。
环境变量:
    TG_API_ID / TG_API_HASH / TG_SESSION
注意: TG_SESSION 是账号级凭据，严禁泄露。
"""

import asyncio
import os
import re
from urllib.parse import urlparse

try:
    # 仅本地调试需要代理（gen_session 同一套环境变量）；Actions 内返回 None，直连 Telegram
    from gen_session import proxy_from_env
except ImportError:  # pragma: no cover
    def proxy_from_env():
        return None


def _client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    api_id, api_hash, session = _credentials()
    return TelegramClient(StringSession(session), api_id, api_hash, proxy=proxy_from_env())


def _credentials():
    api_id = int(os.environ.get("TG_API_ID") or 0)
    api_hash = os.environ.get("TG_API_HASH") or ""
    session = os.environ.get("TG_SESSION") or ""
    missing = [k for k, v in (("TG_API_ID", api_id), ("TG_API_HASH", api_hash), ("TG_SESSION", session)) if not v]
    if missing:
        raise ValueError(f"缺少 Telegram 环境变量: {', '.join(missing)}")
    return api_id, api_hash, session


def normalize_channel(channel):
    """支持 @name / name / https://t.me/name 等写法，统一为 @name。"""
    channel = str(channel).strip()
    if channel.startswith(("https://", "http://")):
        path = urlparse(channel).path.strip("/")
        return f"@{path.split('/')[0]}"
    if channel.startswith(("@", "-")):
        return channel
    return f"@{channel}"


def find_latest_ipa(channel, filename_pattern=r"(?i)\.ipa$", limit=100):
    """扫描公开频道最近 limit 条消息（新到旧），返回最新匹配的文件信息（不下载）。

    返回 dict: file_name / size / date / caption / message；未找到返回 None。
    注意: 返回的 message 对象仅可传给 download_message_file（内部重新建连下载），不可复用。
    """
    from telethon.tl.types import DocumentAttributeFilename

    pattern = re.compile(filename_pattern)

    async def _run():
        async with _client() as client:
            async for msg in client.iter_messages(normalize_channel(channel), limit=limit):
                doc = msg.document
                if doc is None:
                    continue
                name = None
                for attr in doc.attributes or []:
                    if isinstance(attr, DocumentAttributeFilename):
                        name = attr.file_name
                        break
                if not name or not pattern.search(name):
                    continue
                return {
                    "file_name": os.path.basename(name),
                    "size": doc.size,
                    "date": msg.date,  # aware datetime (UTC)
                    "caption": (msg.message or "").strip(),
                    "message": msg,
                }
        return None

    return asyncio.run(_run())


def download_message_file(info, dest_dir):
    """下载 find_latest_ipa 返回的文件到 dest_dir，返回实际落盘路径。"""

    async def _run():
        async with _client() as client:
            path = await client.download_media(info["message"], file=dest_dir)
        return path

    path = asyncio.run(_run())
    if not path:
        raise ValueError("下载失败（消息中无可下载的文件）")
    return os.path.abspath(str(path))
