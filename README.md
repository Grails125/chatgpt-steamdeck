# ChatGPT on Steam Deck

SteamOS 桌面模式一键安装，支持游戏模式启动、X11、自动全屏、签名验证更新及启动确认后的清理。非 OpenAI 或 Valve 官方项目。

本项目只发布安装器和配置方法。ChatGPT 程序由安装器从 OpenAI 的 Linux 软件源下载，使用上游自己的许可及服务条款；MIT 许可仅适用于本仓库代码。仓库不包含账号、聊天记录、个人插件、代理设置或重新分发的 ChatGPT 二进制。

## 安装

在 SteamOS **桌面模式**打开 Konsole，以普通用户运行，不要使用 sudo：

```bash
curl -fL https://raw.githubusercontent.com/Grails125/chatgpt-steamdeck/v0.1.1/install.sh -o /tmp/chatgpt-deck-install.sh
bash /tmp/chatgpt-deck-install.sh
```

也可以下载仓库源码，先检查 `install.sh`，再执行 `bash install.sh`。安装器不会关闭 SteamOS 只读保护或修改系统软件包。需要 x86_64、Python 3.11+、curl、gpgv、ar、bsdtar、ldd、xdotool、zenity，以及能运行官方 Linux Electron 程序的系统库。约需 4 GiB 临时可用空间。`bash install.sh --check` 仅检查依赖。

缺少依赖时安装器会停止并列出名称；不要随意在未知 SteamOS 版本上解锁系统安装依赖。请在该 SteamOS 版本确认依赖可用后再安装。

安装完成后，在应用菜单打开 **ChatGPT (Steam Deck)**。首次登录使用你自己的账号。安装器不迁移已有登录信息；同一用户已有的 `~/.config/Codex` 和 `~/.codex` 可能被上游应用继续使用。

## 加入 Steam 与游戏模式

在桌面模式找到 ChatGPT (Steam Deck) 快捷方式，右键选择“添加到 Steam”；也可在 Steam 的“添加非 Steam 游戏”中选择它。目标是 `~/.local/bin/chatgpt-deck`，启动参数是 `launch`。**不启用 Proton 兼容层**。

回到游戏模式启动。启动器清除 Steam 注入的库变量，以 X11 运行，并在确认所属窗口后发送一次 F11。Steam+X 可打开屏幕键盘。若全屏未生效，可用键盘 F11 切换。

中文输入仍取决于系统输入法及屏幕键盘，不能仅靠应用启动参数保证。本安装器不替换 IBus/Fcitx，不安装 Steam 键盘桥接或语音输入。麦克风、蓝牙和外接设备使用 SteamOS 自身设置。

## 更新、清理与回退

每日定时任务只检查更新并通知，不会在游戏中静默替换应用。关闭本安装器启动的所有 ChatGPT 窗口后，使用菜单里的 **ChatGPT Update (Steam Deck)**，或：

```bash
~/.local/bin/chatgpt-deck check
~/.local/bin/chatgpt-deck update
```

更新需在对话框中确认；终端自动化可用 `update --yes`。软件源的 InRelease 签名、Packages 的 SHA-256、Debian 包大小及 SHA-256 都必须通过验证。固定签名指纹为 `3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4`；上游换钥时会停止，需人工审查更新安装器。

安装器解包官方 Debian 包，不运行其系统安装脚本，也不反复制作 AppImage。程序目录按版本保存，通过原子替换 `current` 链接切换。暂存安装包和解包数据保留到新版本的所属窗口持续可见且进程运行 60 秒；之后删除该次暂存文件及本安装器管理的上一版本。

这项检查表示窗口启动成功，不证明登录、语音及所有功能均正常。确认后旧版不可本地回退。启动失败或未能确认窗口时不清理；关闭窗口后执行：

```bash
~/.local/bin/chatgpt-deck rollback
```

回退保留失败版本供排错。首次安装没有上一版本。旧的 `~/Applications/ChatGPT.AppImage` 安装及其历史文件不由本项目删除；若从旧方案迁移，先关闭旧应用、停用旧更新 timer，并改用新快捷方式，避免两个更新器同时工作。

## 文件与运行配置

| 路径 | 用途 |
|---|---|
| `~/.local/bin/chatgpt-deck` | 安装、启动、更新、检查和回退入口 |
| `~/.local/lib/chatgpt-steamdeck/` | 本项目代码与上游公开验证密钥 |
| `~/.local/share/chatgpt-steamdeck/versions/` | 解包后的官方程序 |
| `~/.local/share/chatgpt-steamdeck/current` | 当前版本链接 |
| `~/.local/share/chatgpt-steamdeck/artifacts/` | 尚待启动确认的安装与解包数据 |
| `~/.local/state/chatgpt-steamdeck/` | 更新事务、锁和启动日志 |
| `~/.config/systemd/user/chatgpt-deck-update.*` | 每日检查服务与定时器 |
| `~/.config/Codex/`、`~/.codex/` | 上游应用个人设置、登录和会话；不打包也不清理 |

默认参数为 `--class=codex-desktop --ozone-platform=x11`。保留 Chromium 沙箱，不添加 `--no-sandbox`。额外启动参数可跟在 `chatgpt-deck launch` 后。本项目不设置代理，沿用用户环境与上游应用设置；不加入额外遥测。

```bash
~/.local/bin/chatgpt-deck doctor
systemctl --user status chatgpt-deck-update.timer
journalctl --user -u chatgpt-deck-update.service
tail -n 80 ~/.local/state/chatgpt-steamdeck/launcher.log
```

启动日志超过 5 MiB 时保留一份轮转文件。停止自动检查使用 `systemctl --user disable --now chatgpt-deck-update.timer`。卸载时先退出应用、停用 timer，再移除表中本项目专属目录和两个桌面快捷方式；保留 `~/.config/Codex` 与 `~/.codex` 即保留个人数据。

## 验证范围

项目源于一台 Steam Deck 的现有 X11 游戏模式安装。版本 0.1.0 改为直接运行经过签名校验的官方解包程序。13 项自动测试覆盖元数据解析、路径约束、更新状态、启动确认清理和回退；已在该 Deck 的隔离用户目录实测下载并校验 `26.915.31945`，通过系统库检查，全新配置窗口持续运行 60 秒后自动清理暂存数据。第二台 Deck 的实际安装、登录与语音功能仍需设备验收；不同 SteamOS 版本的依赖差异会由预检报告。

## English

A user-local Steam Deck installer for the official Linux ChatGPT payload. Run the two installation commands above from Desktop Mode, then add **ChatGPT (Steam Deck)** to Steam without Proton. No root access or read-only filesystem changes are performed. Downloads are verified against the pinned upstream signing key and signed SHA-256 metadata. Updates are user-confirmed; installers, staging files and the managed previous version are deleted only after the new process has displayed its own window for 60 seconds. Failed/unconfirmed launches retain rollback data. Personal profiles and credentials are never bundled or removed. This is an independent community integration, not an official OpenAI/Valve product.
