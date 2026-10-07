# 自建全能签/轻松签/易能签软件源

静态 JSON 源，托管在本仓库（`app.json`），GitHub Actions 每日自动从各 App 的原始更新来源（ESign/AltStore 未加密 JSON 源、GitHub Release、Telegram 公开频道）抓取新版本，需要时把 IPA 本体自动转存到 Cloudflare R2 生成永久 HTTPS 直链。

## 文件结构

```
app.json                      # 最终生成的源文件（被全能签/轻松签添加）
.github/workflows/update.yml  # 每日定时任务
scripts/
  apps.config.json            # 源信息 + App 配置（手动维护）
  build.py                    # 主脚本：抓更新源 → 转存 R2 → 生成 app.json
  tg_monitor.py               # Telegram 频道 IPA 抓取模块
  gen_session.py              # 本地生成 Telethon StringSession 的辅助脚本
  requirements.txt            # Python 依赖
```

## 快速开始

### 1. 创建 GitHub 仓库并推送

```bash
git init && git add -A && git commit -m "init"
git remote add origin git@github.com:YOUR_NAME/YOUR_REPO.git
git push -u origin main
```

### 2. 配置 Cloudflare R2（IPA 转存位置）

1. Cloudflare Dashboard → R2 → 创建 Bucket（记下桶名）
2. Bucket → Settings → **Public access**：允许 `r2.dev` 公开访问（或绑定自定义域），记下公开基础 URL，形如 `https://pub-xxxxxxxx.r2.dev`
3. R2 → Overview → Manage R2 API Tokens → 创建 API Token，权限选 **Object Read & Write**，限定上述桶，得到 Access Key ID / Secret Access Key，并记下 Account ID

### 3. 申请 Telegram API 凭据（仅当收录 TG 来源的 App）

1. 访问 https://my.telegram.org → API development tools → 创建应用，得到 `api_id` / `api_hash`
2. 本地生成 StringSession（账号级凭据，**严禁泄露**）：

```bash
pip install telethon
python scripts/gen_session.py
```

3. 把 `api_id`、`api_hash`、StringSession 分别存入仓库 Secrets

### 4. 配置 Secrets（仓库 Settings → Secrets and variables → Actions）

| Secret 名 | 说明 |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `R2_ACCESS_KEY_ID` | R2 API Token 的 Access Key ID |
| `R2_SECRET_ACCESS_KEY` | R2 API Token 的 Secret Access Key |
| `R2_BUCKET` | R2 桶名 |
| `TG_API_ID` | Telegram api_id（不用 TG 来源可省略） |
| `TG_API_HASH` | Telegram api_hash |
| `TG_SESSION` | Telethon StringSession |

### 5. 填写 App 配置

编辑 `scripts/apps.config.json`（字段说明见下文），然后手动触发一次 Actions（`workflow_dispatch`）或本地运行：

```bash
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=...
export TG_API_ID=... TG_API_HASH=... TG_SESSION=...   # 仅 TG 来源需要
python scripts/build.py
```

### 6. 添加到全能签/轻松签

源地址（必须 HTTPS）：

```
https://raw.githubusercontent.com/YOUR_NAME/YOUR_REPO/main/app.json
```

国内网络如果访问 raw.githubusercontent.com 不稳定，可用 jsDelivr 地址：

```
https://cdn.jsdelivr.net/gh/YOUR_NAME/YOUR_REPO@main/app.json
```

（jsDelivr 有缓存，更新后约 12 小时内生效；加速刷新可访问 `https://purge.jsdelivr.net/gh/YOUR_NAME/YOUR_REPO@main/app.json`）

## apps.config.json 字段说明

```jsonc
{
  "source": {                      // 生成到 app.json 顶层的源信息
    "name": "源名称",
    "identifier": "源唯一标识",
    "sourceURL": "本源自身的访问地址",
    "iconURL": "", "website": "", "tintColor": ""
  },
  "r2": {
    "publicBaseURL": "https://pub-xxx.r2.dev",  // R2 公开访问基础地址
    "keyPrefix": "ipa",                          // R2 对象 key 前缀
    "bucket": "qnz-ipa"                          // 兜底桶名（Secrets 未配置时用）
  },
  "apps": [
    {
      "id": "唯一ID",
      "name": "App 显示名",
      "updateSource": {                 // 方式一：普通 JSON 更新源
        "type": "esign",                // esign | altstore（均需未加密 JSON）
        "url": "https://xxx/app.json",
        "bundleIdentifier": ""          // 留空则自动从更新源读取
      },
      "telegram": null
    },
    {
      "id": "唯一ID",
      "name": "App 显示名",
      "updateSource": {                 // 方式二：GitHub Release
        "type": "github-release",
        "url": "https://github.com/owner/repo/releases",  // 或简写 owner/repo
        "assetPattern": "(?i)\\.ipa$",  // 匹配 Release 附件，默认取 .ipa 结尾的
        "rehost": false                 // false=直接引用 GitHub 直链(永久有效)；true=转存 R2
      },
      "bundleIdentifier": "",           // 留空则首次运行时从 IPA 自动解析
      "telegram": null
    },
    {
      "id": "唯一ID",
      "name": "App 显示名",
      "updateSource": null,
      "telegram": {                     // 方式三：Telegram 公开频道
        "channel": "@channel_name",
        "filenamePattern": "(?i)^xxx.*\\.ipa$",  // 匹配消息中的文件名
        "versionPattern": "(\\d+(?:\\.\\d+)+)",  // 从文件名提取版本号
        "limit": 100                    // 回溯检查的消息条数
      },
      "bundleIdentifier": "com.xxx.xxx" // TG 消息里没有：可手填，或留空首次运行时自动解析
    }
  ]
}
```

`iconURL`、`tintColor`、`developerName`、`localizedDescription`、`versionDescription` 为可选，留空时优先取更新源里的值。

补充规则：

- 同一个 TG 频道发多个 App 时，每个 App 必须配不同的 `filenamePattern` 区分（如 `(?i)maxtube.*\.ipa$`），否则会互相串台。
- 任一 App 加 `"enabled": false` 可临时停用（保留配置、Actions 跳过不报错），需要时改回 `true`。

## 工作原理与格式要点

- 每天 UTC 20:00（北京时间 4:00）Actions 运行 `build.py`；也可在 Actions 页面手动触发。
- GitHub Release 来源默认直接引用 GitHub 直链（Release 资产永久有效，不占用 R2）；`rehost: true` 可改为转存 R2。`bundleIdentifier` 留空时会下载一次 IPA 从 Info.plist 自动解析。
- TG 来源每个新版本下载后自动解析 `bundleIdentifier`（若未配置）并转存 R2（TG 文件链接会过期，必须转存）。
- 其余来源的每个新版本 IPA 下载后上传到 R2：`{keyPrefix}/{bundleIdentifier}/{version}.ipa`，按版本隔离、不覆盖历史版本。
- `app.json` 中 `downloadURL` 写作 `https://pub-xxx.r2.dev/ipa/xxx/1.2.3.ipa` 同样支持的 `{version}` 占位符形式（客户端会自动替换为 `version` 字段的值）。
- 版本号与现有 `app.json` 相同的条目直接跳过下载，脚本幂等。
- **加密源（形如 `source[...]`）无法自动解析**，请使用未加密 JSON 的 ESign/AltStore 源。
- `TG_SESSION` 是 Telegram 账号级凭据，等同账号密码，**严禁提交进仓库或泄露给他人**。
- 源地址必须 HTTPS，否则全能签/轻松签无法添加。

## 依赖

Python 3.10+，`pip install -r scripts/requirements.txt`（requests、boto3、telethon）。
