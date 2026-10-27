# tomoya06 创意软件工坊

自建全能签/轻松签软件源：GitHub Actions 定时从各 App 的原始来源抓取新版本，自动转存生成永久直链并输出静态 `app.json`。

## 添加源

在全能签/轻松签中添加源地址：

```
https://raw.githubusercontent.com/tomoya06/esign-tmy-sources/main/app.json
```

## 收录应用

| App | 简介 |
| --- | --- |
| DjuuMusic | DJ呦呦（m.djuu.com）第三方 iOS 原生客户端（自研，液态玻璃 UI） |
| Spotify | 增强版（EeveeSpotifyReincarnated）：解锁 Premium |
| MaxTube | YouTube 增强版（免广告、后台播放） |
| RyukGram | Instagram 增强版 |
| SoundCloud | 增强版（NexaSC）：解锁 SoundCloud Go+ |
| PiliPlus | 第三方 Bilibili 客户端（Flutter），无广告、界面简洁 |
| RedditDeluxe | 官方 Reddit 客户端注入增强 tweak（RedditFilter） |
| AirCard | Apple Wallet 卡片皮肤与锁屏密码主题 |
| PhotosBackup | 照片备份工具 |

## 最近更新

<!-- changelog:start -->
### 2026-10-27 14:02 · schedule

| App | 结果 | 版本 |
|---|---|---|
| DjuuMusic | ✅ 无更新 | 1.0.6 |
| PhotosBackup | ✅ 无更新 | 0.3.7 |
| RedditDeluxe | ✅ 无更新 | 2026.40.0 |
| MaxTube | ✅ 无更新 | 21.42.4_6.0b2 |
| RyukGram | ✅ 无更新 | 1.5.0 |
| SoundCloud | ✅ 无更新 | 8.75.0-NexaSCb4 |
| AirCard | ✅ 无更新 | 1.3.2 |
| PiliPlus | ✅ 无更新 | 2.1.6.2 |
| Spotify | ✅ 无更新 | 9.1.88 |

共 9 个 App：更新 0 · 无更新 9 · 失败 0 · 缺片段 0

**[完整更新日志 →](CHANGELOG.md)**
<!-- changelog:end -->

## 工作机制

- 北京时间 9/12/15/18/21/24 点各扫描一轮；版本未变时只检查不下载，幂等
- 来源类型：未加密 JSON 源（ESign/AltStore）、GitHub Release、Telegram 公开频道
- IPA 转存到本仓库 `ipa-store` Release 资产（TG 文件链接会过期必须转存；GitHub Release 直链永久有效则直接引用）
- 所有 `github.com` 下载链接在输出层自动加 gh-proxy 镜像前缀，提升国内可达性
- 每轮检查结果写入 [CHANGELOG.md](CHANGELOG.md)（保留最近 60 轮）并刷新上方"最近更新"区块

## 维护

- App 配置：`scripts/apps.config.json`——新增/停用 App、切换镜像（`downloadMirror`）、修改展示信息（图标/描述/主题色）都改这一个文件，下一轮扫描自动生效
- 手动触发：Actions → `update.yml` → Run workflow；本地调试 `python scripts/build.py`
- 密钥仅存仓库 Secrets（`TG_*` 为 Telegram 凭据，严禁泄露）
