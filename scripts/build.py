#!/usr/bin/env python3
"""自建源构建脚本：抓取各 App 原始更新来源 -> 新版本转存 R2 -> 生成 app.json

用法:
    python scripts/build.py
环境变量:
    R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET  R2 转存凭据
    TG_API_ID / TG_API_HASH / TG_SESSION                                 Telegram 凭据(仅 TG 来源需要)
"""

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

try:
    import boto3
except ImportError:
    boto3 = None

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "apps.config.json"
APP_JSON_PATH = ROOT / "app.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tg_monitor as tgm  # noqa: E402

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
TIMEOUT = 60


def log(app_id, msg):
    print(f"[{app_id}] {msg}")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sanitize_version(version):
    """版本号只保留安全字符，保证 R2 key 与 downloadURL 占位符替换后一致。"""
    version = str(version).strip()
    return re.sub(r"[^A-Za-z0-9._-]", "-", version) or "0"


def fetch_json(url):
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def pick_esign(data, wanted_bid):
    """解析 ESign/轻松签格式源，返回 (bundle_identifier, meta dict)。"""
    apps = data.get("apps") or []
    if not apps:
        raise ValueError("更新源 apps 为空")
    target = None
    if wanted_bid:
        for a in apps:
            if a.get("bundleIdentifier") == wanted_bid:
                target = a
                break
        if target is None:
            raise ValueError(f"更新源中找不到 bundleIdentifier={wanted_bid}")
    else:
        target = apps[0]
    bid = target.get("bundleIdentifier") or ""
    if not bid:
        raise ValueError("更新源条目缺少 bundleIdentifier")
    return bid, {
        "version": target.get("version"),
        "versionDate": target.get("versionDate"),
        "versionDescription": target.get("versionDescription", ""),
        "downloadURL": target.get("downloadURL"),
        "size": target.get("size"),
        "iconURL": target.get("iconURL", ""),
        "tintColor": target.get("tintColor", ""),
        "developerName": target.get("developerName", ""),
        "localizedDescription": target.get("localizedDescription", ""),
    }


def parse_github_repo(ref):
    """支持 owner/repo 与 https://github.com/owner/repo(/releases...) 两种写法。"""
    ref = str(ref).strip().rstrip("/")
    m = re.match(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)", ref)
    if m:
        repo = m.group(2)
        return m.group(1), repo[:-4] if repo.endswith(".git") else repo
    parts = ref.strip("/").split("/")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    raise ValueError(f"无法识别 GitHub 仓库: {ref}")


def gh_headers(token):
    return {"Accept": "application/vnd.github+json", "User-Agent": UA, "Authorization": f"Bearer {token}"}


def gh_api(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    token = os.environ.get("GITHUB_TOKEN") or ""  # Actions 内置 token，避免匿名限额
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def pick_github_release(src):
    """抓取仓库最新 Release（不含 prerelease/draft），返回 meta dict。"""
    owner, repo = parse_github_repo(src["url"])
    rel = gh_api(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
    pattern = src.get("assetPattern") or r"(?i)\.ipa$"
    assets = [a for a in rel.get("assets") or [] if re.search(pattern, a.get("name", ""))]
    if not assets:
        raise ValueError(f"Release {rel.get('tag_name')} 中没有匹配 {pattern} 的 asset")
    asset = assets[0]
    tag = str(rel.get("tag_name") or "").strip()
    version = tag[1:] if tag[:1] in "vV" else tag
    body = (rel.get("body") or "").strip()
    return {
        "version": version,
        "versionDate": rel.get("published_at") or rel.get("created_at"),
        "versionDescription": body,
        "downloadURL": asset.get("browser_download_url"),
        "size": asset.get("size"),
        "iconURL": "", "tintColor": "", "developerName": "",
        "localizedDescription": body,
    }


def resolve_bundle_id_from_ipa_file(ipa_path):
    """从已下载的 IPA 内 Payload/*.app/Info.plist 解析 CFBundleIdentifier。"""
    with zipfile.ZipFile(ipa_path) as z:
        for name in z.namelist():
            if name.startswith("Payload/") and name.endswith(".app/Info.plist") and name.count("/") == 2:
                with z.open(name) as f:
                    bid = str(plistlib.load(f).get("CFBundleIdentifier") or "").strip()
                if bid and "$(" not in bid:
                    return bid
    raise ValueError("无法从 IPA 解析 bundleIdentifier，请在配置中手填")


def resolve_bundle_id_from_ipa(url):
    """下载 IPA 并解析 CFBundleIdentifier（bundleIdentifier 未配置时用）。"""
    with tempfile.TemporaryDirectory() as tmp:
        ipa_path = Path(tmp) / "probe.ipa"
        download_to_file(url, ipa_path)
        return resolve_bundle_id_from_ipa_file(ipa_path)


def _parse_alt_date(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def pick_altstore(data, wanted_bid):
    """解析 AltStore 格式源，返回 (bundle_identifier, meta dict)。"""
    apps = data.get("apps") or []
    if not apps:
        raise ValueError("更新源 apps 为空")
    target = None
    if wanted_bid:
        for a in apps:
            if a.get("bundleIdentifier") == wanted_bid:
                target = a
                break
    else:
        target = apps[0]
    if target is None:
        raise ValueError(f"更新源中找不到 bundleIdentifier={wanted_bid}")
    versions = target.get("versions") or []
    if not versions:
        raise ValueError("AltStore 条目 versions 为空")
    latest = max(versions, key=lambda v: _parse_alt_date(v.get("date")))
    bid = target.get("bundleIdentifier") or ""
    if not bid:
        raise ValueError("更新源条目缺少 bundleIdentifier")
    return bid, {
        "version": latest.get("version"),
        "versionDate": latest.get("date"),
        "versionDescription": latest.get("localizedDescription", ""),
        "downloadURL": latest.get("downloadURL"),
        "size": latest.get("size"),
        "iconURL": target.get("iconURL", ""),
        "tintColor": target.get("tintColor", ""),
        "developerName": target.get("developerName", ""),
        "localizedDescription": target.get("localizedDescription", ""),
    }


def r2_client(cfg):
    if boto3 is None:
        return None, "未安装 boto3 (pip install boto3)"
    account = os.environ.get("R2_ACCOUNT_ID", "")
    key_id = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    if not (account and key_id and secret):
        return None, "缺少 R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY"
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    bucket = os.environ.get("R2_BUCKET") or (cfg.get("r2", {}) or {}).get("bucket", "")
    return (client, bucket), None


def r2_download_url(cfg, bucket, bid, version):
    """生成 downloadURL：使用 {version} 占位符，客户端下载时自动替换。"""
    r2cfg = cfg.get("r2", {}) or {}
    base = (r2cfg.get("publicBaseURL") or "").rstrip("/")
    prefix = (r2cfg.get("keyPrefix") or "ipa").strip("/")
    return f"{base}/{quote(prefix)}/{quote(bid)}/{{version}}.ipa"


def rehost_to_r2(app_id, bid, version, source_url):
    """下载 source_url 的 IPA 并转存 R2，返回 (downloadURL, size, err)。"""
    r2, err = r2_client(CFG)
    if r2 is None:
        return None, 0, err
    client, bucket = r2
    key = f"{(CFG.get('r2', {}) or {}).get('keyPrefix', 'ipa').strip('/')}/{bid}/{version}.ipa"
    with tempfile.TemporaryDirectory() as tmp:
        ipa_path = Path(tmp) / f"{bid}_{version}.ipa"
        log(app_id, f"下载 v{version} -> R2:{key}")
        size = download_to_file(source_url, ipa_path)
        client.upload_file(str(ipa_path), bucket, key)
    return r2_download_url(CFG, bucket, bid, version), size, None


def gh_storage_repo(st):
    """确定 IPA 存储用的 GitHub 仓库：config > GITHUB_REPOSITORY > git remote。"""
    repo = st.get("repo") or os.environ.get("GITHUB_REPOSITORY") or ""
    if repo:
        return repo
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?/?$", out.stdout.strip())
        if m:
            return m.group(1)
    except OSError:
        pass
    raise ValueError("无法确定存储仓库，请在 storage.repo 中指定")


def gh_token():
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise ValueError("GitHub Release 存储需要 GITHUB_TOKEN（Actions 内置；本地: export GITHUB_TOKEN=$(gh auth token)）")
    return token


def ensure_gh_release(repo, token, tag):
    """获取（或首次创建）存储用 Release 的 id。"""
    headers = gh_headers(token)
    resp = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", headers=headers, timeout=TIMEOUT)
    if resp.status_code == 200:
        return resp.json()["id"]
    if resp.status_code != 404:
        resp.raise_for_status()
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        json={"tag_name": tag, "name": tag, "body": "IPA 转存（由 build.py 自动维护，请勿手动修改）"},
        headers=headers, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def gh_upload_asset(repo, token, release_id, name, path):
    """上传 asset（同名先删），返回资产大小。"""
    headers = gh_headers(token)
    resp = requests.get(f"https://api.github.com/repos/{repo}/releases/{release_id}/assets?per_page=100", headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    for a in resp.json():
        if a.get("name") == name:
            resp = requests.delete(a["url"], headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
    with open(path, "rb") as f:
        resp = requests.post(
            f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={quote(name)}",
            headers={**headers, "Content-Type": "application/octet-stream"},
            data=f, timeout=1800,
        )
    resp.raise_for_status()
    return resp.json().get("size", Path(path).stat().st_size)


def storage_upload_file(app_id, bid, version, ipa_path):
    """上传本地 IPA 到配置的存储（github-release / r2），返回 downloadURL。

    asset/key 名含 app_id 前缀：同一 bundleId 的多个变体（如 RyukGram 完整版与
    No-Plugins 版）不会互相覆盖。
    """
    st = CFG.get("storage", {}) or {}
    if st.get("type") == "github-release":
        repo = gh_storage_repo(st)
        token = gh_token()
        tag = st.get("tag") or "ipa-store"
        release_id = ensure_gh_release(repo, token, tag)
        name = f"{app_id}_{bid}_{version}.ipa"
        size = gh_upload_asset(repo, token, release_id, name, ipa_path)
        log(app_id, f"已上传 GitHub Release:{tag}/{name} ({size} bytes)")
        return f"https://github.com/{repo}/releases/download/{tag}/{name}"
    r2, err = r2_client(CFG)
    if r2 is None:
        raise ValueError(f"存储不可用: {err}")
    client, bucket = r2
    key = f"{(CFG.get('r2', {}) or {}).get('keyPrefix', 'ipa').strip('/')}/{app_id}/{bid}/{version}.ipa"
    log(app_id, f"上传 -> R2:{key}")
    client.upload_file(str(ipa_path), bucket, key)
    return r2_download_url(CFG, bucket, bid, version)


def rehost_to_storage(app_id, bid, version, source_url):
    """下载 source_url 的 IPA 并转存到配置的存储，返回 (downloadURL, size, err)。"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ipa_path = Path(tmp) / f"{bid}_{version}.ipa"
            log(app_id, f"下载 v{version} 到临时目录")
            size = download_to_file(source_url, ipa_path)
            url = storage_upload_file(app_id, bid, version, ipa_path)
        return url, size, None
    except Exception as exc:
        return None, 0, str(exc)


def download_to_file(url, dest):
    with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return Path(dest).stat().st_size


def build_entry(app_cfg, bid, meta, download_url, size):
    entry = {
        "name": app_cfg.get("name") or "",
        "bundleIdentifier": bid,
        "developerName": app_cfg.get("developerName") or meta.get("developerName") or "",
        "version": str(meta.get("version") or ""),
        "versionDate": meta.get("versionDate") or datetime.now(timezone.utc).isoformat(),
        "versionDescription": app_cfg.get("versionDescription") or meta.get("versionDescription") or "",
        "downloadURL": download_url,
        "localizedDescription": app_cfg.get("localizedDescription") or meta.get("localizedDescription") or "",
        "iconURL": app_cfg.get("iconURL") or meta.get("iconURL") or "",
        "tintColor": app_cfg.get("tintColor") or meta.get("tintColor") or "",
        "size": int(size or 0),
        "type": 1,
    }
    return entry


def process_json_app(app_cfg, existing_by_bid):
    bid_cfg = (app_cfg.get("updateSource") or {}).get("bundleIdentifier", "")
    src = app_cfg["updateSource"]
    data = fetch_json(src["url"])
    if src.get("type") == "altstore":
        bid, meta = pick_altstore(data, bid_cfg)
    else:
        bid, meta = pick_esign(data, bid_cfg)
    existing = existing_by_bid.get(bid)  # JSON 模式的 bid 来自更新源，抓到后再查旧条目
    if not meta.get("version") or not meta.get("downloadURL"):
        raise ValueError("更新源缺少 version 或 downloadURL")
    if existing and existing.get("version") == str(meta["version"]):
        log(app_cfg["id"], f"无更新 (v{meta['version']})")
        return None  # 无变化，沿用现有条目

    version = sanitize_version(meta["version"])
    url, size, err = rehost_to_storage(app_cfg["id"], bid, version, meta["downloadURL"])
    if err:
        # 转存失败：JSON 源退回原始直链（可能失效），TG 源必须转存
        log(app_cfg["id"], f"转存失败({err})，退回原始直链")
        return build_entry(app_cfg, bid, meta, meta["downloadURL"], meta.get("size"))
    log(app_cfg["id"], f"已上传 (v{meta['version']}, {size} bytes)")
    return build_entry(app_cfg, bid, meta, url, size)


def process_gh_release(app_cfg, existing):
    src = app_cfg["updateSource"]
    meta = pick_github_release(src)
    version = sanitize_version(meta["version"])
    if existing and existing.get("version") == str(meta["version"]):
        log(app_cfg["id"], f"无更新 (v{meta['version']})")
        return None

    bid = app_cfg.get("bundleIdentifier") or ""
    if not bid:  # GitHub Release 元数据不含 bundleId，未配置时从 IPA 自动解析
        log(app_cfg["id"], "未配置 bundleIdentifier，从 IPA 自动解析")
        bid = resolve_bundle_id_from_ipa(meta["downloadURL"])
        log(app_cfg["id"], f"解析到 bundleIdentifier: {bid}")

    if not src.get("rehost"):  # Release 直链永久有效，默认直接引用不转存
        log(app_cfg["id"], f"使用 GitHub 直链 (v{meta['version']})")
        return build_entry(app_cfg, bid, meta, meta["downloadURL"], meta.get("size"))

    url, size, err = rehost_to_storage(app_cfg["id"], bid, version, meta["downloadURL"])
    if err:
        raise ValueError(f"rehost=true 但转存失败: {err}")
    log(app_cfg["id"], f"已转存 (v{meta['version']}, {size} bytes)")
    return build_entry(app_cfg, bid, meta, url, size)


def process_tg_app(app_cfg, existing):
    tg_cfg = app_cfg["telegram"]
    bid = app_cfg.get("bundleIdentifier")
    if not bid and existing and existing.get("bundleIdentifier"):
        bid = existing["bundleIdentifier"]  # 首次解析后沿用旧条目的 bid；首次无旧条目则下载后再解析
    vp = tg_cfg.get("versionPattern")
    info = tgm.find_latest_ipa(
        channel=tg_cfg.get("channel", ""),
        filename_pattern=tg_cfg.get("filenamePattern") or r"(?i)\.ipa$",
        version_pattern=vp,  # 提供时在全部匹配中取版本号最大的（同轮多 iOS 基线发布）
        limit=int(tg_cfg.get("limit") or 100),
    )
    if info is None:
        raise ValueError("频道中未找到匹配的 .ipa 文件")

    m = re.search(vp, info["file_name"]) if vp else re.search(r"(\d+(?:\.\d+)+)", info["file_name"])
    version = m.group(1) if m else info["date"].strftime("%Y%m%d")
    version = sanitize_version(version)
    if existing and existing.get("version") == version:
        log(app_cfg["id"], f"无更新 (v{version})")
        return None

    tmpdir = tempfile.mkdtemp()
    try:
        log(app_cfg["id"], f"下载 {info['file_name']} 到临时目录")
        downloaded = tgm.download_message_file(info, tmpdir)
        ipa_path = Path(downloaded)
        size = ipa_path.stat().st_size
        if not bid:  # TG 消息不含 bundleId，未配置时从 IPA 自动解析
            log(app_cfg["id"], "未配置 bundleIdentifier，从 IPA 自动解析")
            bid = resolve_bundle_id_from_ipa_file(ipa_path)
            log(app_cfg["id"], f"解析到 bundleIdentifier: {bid}")
        url = storage_upload_file(app_cfg["id"], bid, version, ipa_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    meta = {
        "version": version,
        "versionDate": info["date"].astimezone(timezone.utc).isoformat(),
        "versionDescription": (info.get("caption") or "")[:500],
        "size": size,
        "iconURL": "", "tintColor": "", "developerName": "", "localizedDescription": "",
    }
    log(app_cfg["id"], f"已转存 (v{version}, {size} bytes)")
    return build_entry(app_cfg, bid, meta, url, size)


def load_existing_maps():
    """读现有 app.json，返回 (by_bundleId, by_name) 两张旧条目查找表。"""
    apps = []
    if APP_JSON_PATH.exists():
        try:
            apps = load_json(APP_JSON_PATH).get("apps") or []
        except (ValueError, OSError):
            log("app.json", "现有 app.json 解析失败，忽略")
    valid = [a for a in apps if isinstance(a, dict)]
    return (
        {a.get("bundleIdentifier"): a for a in valid},
        {a.get("name"): a for a in valid},
    )


def find_existing(app_cfg, existing_by_bid, existing_by_name):
    """预查旧条目：TG 模式用配置的 bid 精确匹配；JSON 模式 bid 在源里，用 name 兜底。"""
    existing = existing_by_bid.get(app_cfg.get("bundleIdentifier") or "")
    if existing is None:
        existing = existing_by_name.get(app_cfg.get("name") or "")
    return existing


STATIC_FIELDS = ("name", "developerName", "localizedDescription", "iconURL", "tintColor", "versionDescription")


def refresh_static_fields(entry, app_cfg):
    """把 config 中的静态展示字段刷新进条目。

    沿用旧条目（无更新/失败回落）时，config 里改名/图标/描述等变更也能生效；
    config 留空的字段不覆盖（保留来源动态值，如 TG caption）。
    """
    if not isinstance(entry, dict):
        return entry
    for f in STATIC_FIELDS:
        v = app_cfg.get(f)
        if v:
            entry[f] = v
    return entry


def process_one(app_cfg, existing, existing_by_bid):
    """处理单个 App，返回 (entry, error)。

    entry 为本次最新条目；无更新或失败时回落 existing（可能为 None）。
    error 非 None 表示本轮处理失败（条目仍是旧值）。
    """
    app_id = app_cfg.get("id") or app_cfg.get("name") or "unknown"
    try:
        if app_cfg.get("updateSource"):
            stype = str((app_cfg["updateSource"] or {}).get("type") or "esign").lower()
            if stype in ("github-release", "githubrelease"):
                entry = process_gh_release(app_cfg, existing)
            else:
                entry = process_json_app(app_cfg, existing_by_bid)
        elif app_cfg.get("telegram"):
            entry = process_tg_app(app_cfg, existing)
        else:
            raise ValueError("配置缺少 updateSource 或 telegram")
        return refresh_static_fields(entry if entry is not None else existing, app_cfg), None
    except Exception as exc:  # 失败容错：保留旧条目，不让单点故障毁掉整个源
        log(app_id, f"失败: {exc}")
        return refresh_static_fields(existing, app_cfg), str(exc)


def apply_download_mirror(url):
    """给 GitHub release 直链套加速镜像前缀（国内下载优化）。

    幂等：无论传入裸链接还是带旧镜像前缀的链接，都归一化为
    <当前镜像><裸链接>；未配置镜像时剥离旧前缀还原直链。
    非 github.com 链接不处理。在 render_source 输出层统一应用，
    因此切换/清除镜像只改 config，下一轮扫描自动生效。
    """
    if not url or "github.com/" not in url:
        return url
    if "https://github.com/" in url:
        bare = "https://github.com/" + url.split("https://github.com/", 1)[1]
    elif "http://github.com/" in url:
        bare = "https://github.com/" + url.split("http://github.com/", 1)[1]
    else:
        return url
    mirror = str(CFG.get("downloadMirror") or "").strip()
    if mirror and not mirror.endswith("/"):
        mirror += "/"
    return mirror + bare if mirror else bare


def render_source(entries):
    src = CFG.get("source", {})
    out = {k: src.get(k, "") for k in ("name", "identifier", "sourceURL")}
    out["apps"] = entries
    for opt in ("iconURL", "website", "tintColor"):
        if src.get(opt):
            out[opt] = src[opt]
    for e in out["apps"]:
        if isinstance(e, dict) and e.get("downloadURL"):
            e["downloadURL"] = apply_download_mirror(e["downloadURL"])
    return out


def enabled_apps():
    return [a for a in CFG.get("apps", []) if a.get("enabled") is not False]


def main():
    """全量模式：一轮处理所有 App 并写出 app.json（本地调试用；Actions 走粒子模式）。"""
    global CFG
    CFG = load_json(CONFIG_PATH)
    existing_by_bid, existing_by_name = load_existing_maps()

    entries, failed = [], []
    for app_cfg in enabled_apps():
        app_id = app_cfg.get("id") or app_cfg.get("name") or "unknown"
        existing = find_existing(app_cfg, existing_by_bid, existing_by_name)
        entry, err = process_one(app_cfg, existing, existing_by_bid)
        if err:
            failed.append(app_id)
        if isinstance(entry, dict):
            entries.append(entry)

    save_json(APP_JSON_PATH, render_source(entries))
    log("summary", f"共 {len(entries)} 个 App，失败 {len(failed)}{': ' + ','.join(failed) if failed else ''}")
    sys.exit(1 if failed else 0)


def cmd_only(app_id, fragment_path):
    """粒子模式：只同步单个 App，把结果写入片段文件供 publish 汇总。

    片段内 entry 为 None 表示本轮未能产出可用条目（失败且无旧条目）。
    """
    global CFG
    CFG = load_json(CONFIG_PATH)
    candidates = [a for a in enabled_apps() if (a.get("id") or a.get("name") or "unknown") == app_id]
    if not candidates:
        raise SystemExit(f"未找到启用的 App 配置: {app_id}")
    existing_by_bid, existing_by_name = load_existing_maps()
    existing = find_existing(candidates[0], existing_by_bid, existing_by_name)
    entry, err = process_one(candidates[0], existing, existing_by_bid)
    if not isinstance(entry, dict):
        entry = None
    frag_path = Path(fragment_path)
    frag_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(frag_path, {"id": app_id, "entry": entry, "error": err})
    log("summary", f"[{app_id}] 片段已写出 -> {frag_path}" + (f" (错误: {err})" if err else ""))
    sys.exit(1 if err else 0)


def cmd_merge(frag_dir):
    """汇总模式：合并各粒子 job 的片段，重建 app.json。

    缺片段（job 崩溃/被取消）或片段无条目时，回落 app.json 旧条目，
    保证单个 App 失败不影响整轮更新。
    """
    global CFG
    CFG = load_json(CONFIG_PATH)
    existing_by_bid, existing_by_name = load_existing_maps()
    frag_dir = Path(frag_dir)

    entries, failed, missing = [], [], []
    for app_cfg in enabled_apps():
        app_id = app_cfg.get("id") or app_cfg.get("name") or "unknown"
        frag = None
        frag_path = frag_dir / f"{app_id}.json"
        if frag_path.exists():
            try:
                frag = load_json(frag_path)
            except (ValueError, OSError):
                frag = None
        entry = frag.get("entry") if isinstance(frag, dict) else None
        if isinstance(entry, dict):
            entries.append(entry)
            continue
        if frag is not None:
            failed.append(app_id)
            log("merge", f"[{app_id}] 片段无可用条目 (error: {frag.get('error')})，回落旧条目")
        else:
            missing.append(app_id)
            log("merge", f"[{app_id}] 缺少片段（job 崩溃或被取消?），回落旧条目")
        existing = find_existing(app_cfg, existing_by_bid, existing_by_name)
        if isinstance(existing, dict):
            entries.append(refresh_static_fields(existing, app_cfg))
        else:
            log("merge", f"[{app_id}] 无旧条目，本轮源中缺席")

    save_json(APP_JSON_PATH, render_source(entries))
    log("summary", f"共 {len(entries)} 个 App；同步失败 {len(failed)}{': ' + ','.join(failed) if failed else ''}"
                   f"；缺片段 {len(missing)}{': ' + ','.join(missing) if missing else ''}（均已回落旧条目）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建 iOS 软件源 app.json")
    parser.add_argument("--only", metavar="APP_ID", help="粒子模式：只同步指定 App，结果写片段文件")
    parser.add_argument("--fragment", metavar="PATH", help="片段输出路径（配合 --only，默认 out/<APP_ID>.json）")
    parser.add_argument("--merge", metavar="DIR", help="汇总模式：合并片段目录并写出 app.json")
    args = parser.parse_args()
    if args.merge:
        cmd_merge(args.merge)
    elif args.only:
        cmd_only(args.only, args.fragment or f"out/{args.only}.json")
    else:
        main()
