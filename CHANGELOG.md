# Changelog

## v0.1.1

### [ZH-CN]

- 首次正式打包发布；安装入口固定到 v0.1.1。
- 发布流程增加手动触发入口，可选择版本标签重新执行发布。

### [EN]

- First packaged release; pin the bootstrap installer to v0.1.1.
- Allow manual release workflow runs against a selected version tag.

## v0.1.0

### [ZH-CN]

- 首个 Steam Deck 用户目录安装器，不需要 root 或修改 SteamOS 只读系统。
- 从官方 Linux 软件源下载，并校验签名、文件大小和 SHA-256。
- X11 游戏模式启动、所属窗口自动全屏、桌面快捷方式及每日更新通知。
- 确认新版本窗口持续运行 60 秒后清理安装包、暂存数据和旧版；失败时保留回退。
- 提供诊断、手动回退、配置说明及隔离测试。

### [EN]

- Initial user-local Steam Deck installer; no root or read-only system changes.
- Official Linux downloads verified with the pinned signing key, size and SHA-256.
- X11 Game Mode launch, automatic fullscreen, desktop entries and daily update checks.
- Cleanup after a confirmed 60-second launch; rollback retained for failed/unconfirmed starts.
- Diagnostics, manual rollback, configuration documentation and isolated tests.
