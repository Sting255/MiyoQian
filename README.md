<h1>米游签</h1>

<div align="center">
  <h1 align="center">
    <img src="./miyouqian/webui/assets/myq_logo.png" width="180" alt="米游签" style="border-radius:10px">
  </h1>
  <p>带 Web 控制台的米游社每日签到工具：扫码登录、游戏社区签到、云游戏签到、米游币任务、商品兑换、每日自动执行、结果推送。</p>
  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&style=flat-square">
    <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-1E9BFA?style=flat-square">
    <img alt="Encoding" src="https://img.shields.io/badge/encoding-UTF--8-2EA44F?style=flat-square">
  </p>
  <p><sub>个人修改版，来源于 <a href="https://github.com/Marchen-orz/MiyoQian">Marchen-orz/MiyoQian</a></sub></p>
</div>

> [!IMPORTANT]
> **本仓库是 [Marchen-orz/MiyoQian](https://github.com/Marchen-orz/MiyoQian) 的衍生版本（个人修改版），不是原创项目。**
>
> - 原项目作者：**[@Marchen-orz](https://github.com/Marchen-orz)** —— 主要功能都是他写的，也由他在维护。
> - **原项目地址：<https://github.com/Marchen-orz/MiyoQian>**
>   想用原版、想提 Issue / PR、想给 Star，**都请去原仓库**。
> - 本仓库只是在他的基础上按个人需要做了一些改动，并会持续跟踪他的更新。

## 目录

- [这是什么](#这是什么)
- [功能](#功能)
- [快速开始](#快速开始)
- [保持运行](#保持运行)
- [Web 控制台](#web-控制台)
- [常用配置](#常用配置)
- [命令行](#命令行)
- [常见问题](#常见问题)
- [其它运行方式](#其它运行方式)
- [关于原项目](#关于原项目)

## 这是什么

用米游社 APP 登录一次（扫码或手机号+短信），之后在本地网页控制台里管理账号、勾任务、设每天自动执行，跑完把结果推给你。

<img src="./assets/home.png" alt="demo" style="max-width:100%;border-radius:10px">

这份个人修改版比原版多出来的主要是这四样：

- **本地验证码识别** —— 用本机 Chrome 直接解极验九宫格，不用付费打码服务（网页上有「环境自检」可先验证环境）
- **出口 IP 防护** —— IP 在境外时暂停签到，避免异地登录风控
- **账号间隔可调** —— 多账号之间随机等多久，直接在网页上改
- **一批修复** —— 推送（兑换消息转义、崩溃/跳过/停止都可见、校验业务返回码、正文长度上限）、调度（配置写错不再静默失效）、抢购（同商品同时间的多账号各跑一条、不再兑换错商品）

## 功能

| 功能 | 说明 |
| --- | --- |
| 扫码 / 短信登录 | 米游社 APP 扫码，或手机号 + 短信验证码 |
| 多账号 | 每个账号可以单独配任务、单独设间隔 |
| 游戏社区签到 | 原神、星穹铁道、绝区零等 |
| 云游戏签到 | 云原神、云绝区零（需要单独配 token） |
| 米游币任务 | 目前实际只有「社区签到」有效 |
| 商品兑换 | 到点自动抢，抢到就停 |
| 每日自动执行 | 固定时间 + 随机波动，避免天天卡整点 |
| 结果推送 | pushplus / QQ / Telegram / 钉钉 / 飞书 / 邮件 |
| IP 防护 / 本地验证码 | 见上面「这是什么」 |

支持的游戏（`games.enabled` 里填这些名字）：

| 游戏 | 配置名 |
| --- | --- |
| 原神 | `genshin` |
| 崩坏：星穹铁道 | `starrail` |
| 绝区零 | `zzz` |
| 崩坏3 | `honkai3rd` |
| 未定事件簿 | `tears` |
| 崩坏学园2 | `honkai2` |

默认启用原神、星穹铁道、绝区零。

## 快速开始

**1. 装 uv 和依赖**（需要 Python 3.11）

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

重新打开终端，进项目目录：

```bash
uv venv --python 3.11
uv sync
```

**2. 启动 Web 控制台**

```powershell
uv run python main.py
```

终端会打印地址，默认 **http://127.0.0.1:5890**（端口被占用会自动往后试 30 个，以终端显示的为准）。

**3. 登录账号**：在「账号」区点 **添加** → 点 **登录** → 选扫码（米游社 APP：我的 → 左上角扫一扫）或短信验证码。登录成功后卡片会显示 UID。

**4. 跑一次**：在「任务配置」里勾选要跑的（第一次建议**只开游戏社区签到**），点右上角 **立即执行**，右侧看日志。提示「今日已签到」就正常。

**5. 开自动执行**：展开「每日调度」→ 勾选 **启用每日自动执行** → 设置时间和随机波动分钟（例如 `09:00` + `30`，表示 09:00~09:30 之间随机挑一刻跑）。

> 凭证存在 `data/credentials.yaml`，**不在 `config.yaml` 里** —— 所以 `config.yaml` 可以分享，凭证文件绝对不能外传。

## 保持运行

关掉终端、关机或休眠，自动任务就不会跑，所以要让进程常驻。

**Windows**：仓库带了两个脚本，用计划任务在登录时启动（**注册需要管理员权限**）：

```powershell
$action   = New-ScheduledTaskAction -Execute 'wscript.exe' `
            -Argument ('//B //Nologo "' + (Resolve-Path .\start-miyoqian-hidden.vbs).Path + '"')
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -DontStopOnIdleEnd -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName 'MiyoQianWebUI' -Action $action -Trigger $trigger -Settings $settings
```

之后用这两条启动 / 停止（启动不需要管理员权限）：

```powershell
Start-ScheduledTask -TaskName MiyoQianWebUI
Stop-ScheduledTask  -TaskName MiyoQianWebUI
```

> ⚠️ 两个坑，照抄时注意：
> 1. **动作别直接写 `powershell.exe`** —— 它会弹控制台窗口，`-WindowStyle Hidden` 也挡不住；所以这里走 `wscript.exe` + `.vbs`。
> 2. **`-DontStopOnIdleEnd` 不能省** —— 默认设置会在"空闲结束"时停止任务，并**连带杀掉整个进程树**（服务凭空消失且日志无报错）。

**Linux / macOS**：用 `systemd --user` 或 `nohup` + cron 常驻即可，`start.sh` 会处理依赖。

## Web 控制台

| 区域 | 用途 |
| --- | --- |
| 顶部状态 | 账号数、调度状态、下次执行时间、最近结果 |
| 账号 | 添加 / 登录 / 刷新凭证 / 改名 / 删除，以及该账号的独立任务 |
| 任务配置 | 游戏社区签到、云游戏签到、米游币任务（所有账号的默认值） |
| 设备指纹 | 查看和更换机型、设备 ID、设备 FP（换了立即生效，不用重启） |
| 每日调度 | 每天什么时候跑，以及账号之间的随机等待 |
| 推送通道 | 通知方式；有「测试推送」可先验证渠道通不通 |
| 验证码识别 | 选本地识别或打码狗；「环境自检」验证本机环境 |
| IP 防护 | 开关、间隔，以及「立即检测出口 IP」 |
| 兑换 | 商品浏览/筛选、自动兑换开关、兑换计划 |
| 日志 | 本次启动后的运行记录（历史在 `logs/miyouqian.log`） |

顶部「停止」按钮可以中断正在跑的任务：账号间等待和等 IP 恢复会立刻中断；正在跑某个账号时会等它当前任务结束再停，不会硬杀进程。停止后仍会推一条结果，标题会写明是手动停止。

多账号时可以在推送通道里勾「每跑完一个账号就推送一次」，不用等全部跑完（账号间隔可能长达 1~2 小时）。注意它和「只在失败时推送」是同一个开关在管：开了 `error_only` 之后，逐账号的**成功**推送也会被跳过。

## 常用配置

改 `config.yaml`（或在网页上改）。最常用的几个：

| 想改什么 | 键 | 默认 |
| --- | --- | --- |
| 总开关 | `enable` | `true` |
| 任务开关 | `features.game_checkin` / `cloud_game_checkin` / `bbs_tasks` | 开 / 关 / 关 |
| 跑哪些游戏 | `games.enabled` | 原神、星铁、绝区零 |
| 每天几点跑 | `schedule.time` + `schedule.jitter_minutes` | `"09:00"` / 45 |
| 账号之间等多久 | `account_gap.min_minutes` / `max_minutes` | 60 / 120 |
| 网页端口 / 密码 | `web.port` / `web.password` | 5890 / 空 |
| 推送 | `push.channels` | 未配置 |
| 验证码 | `captcha.channels` | 都关 |
| IP 防护 | `ip_guard.enable` | 开 |
| 商品兑换 | `shop_exchange` | 关 |

> `schedule.time` 记得加引号写成 `"09:00"`；不加引号 YAML 会当成数字 `540`（程序能认出来，但写清楚更好）。

**完整的配置项、每个键的含义、验证码与 IP 防护的细节，都在 [docs/configuration.md](docs/configuration.md)。**

## 命令行

```text
uv run python main.py [-c 配置文件] <子命令> [参数]
```

| 子命令 | 作用 |
| --- | --- |
| `init` | 生成默认配置（`--force` 覆盖） |
| `login` | 扫码登录并写凭证（`--account 名字`、`--timeout 秒`、`--no-image`） |
| `run` | 跑签到任务（见下表） |
| `serve` | 启动常驻控制台（`--host` / `--port` 覆盖配置） |
| `show` | 打印当前配置摘要 |

`run` 的参数：`--account 名字`（只跑一个账号）、`--games-only`（只跑游戏+云游戏）、`--bbs-only`（只跑米游币，与前者互斥）、`--game 游戏名`（可重复传）。

```powershell
uv run python main.py init
uv run python main.py login --account 大号
uv run python main.py run
uv run python main.py run --games-only
uv run python main.py serve --port 5891
```

`run` **按结果返回退出码**（成功 `0`、失败 `1`），cron / 计划任务 / CI 可以直接用它判断要不要告警。

## 常见问题

- **凭证存在哪？** `data/credentials.yaml`，不在 `config.yaml` 里 —— 别外传。
- **Web 控制台打不开？** 看终端打印的实际地址；5890 被占用会自动换端口。
- **自动调度没执行？** 程序必须一直运行（见[保持运行](#保持运行)），并且「每日调度」里要勾选启用。
- **遇到验证码？** 见 [docs/configuration.md](docs/configuration.md) 里的验证码一节（本地识别免费，打码狗付费，二选一）。
- **米游币任务失败，游戏签到正常？** 米游币更容易触发风控，属正常，低频使用。
- 更多问答见 **[docs/faq.md](docs/faq.md)**。

## 其它运行方式

不想在本机常驻的话：

| 方式 | 说明 |
| --- | --- |
| [Docker](docs/deploy.md#docker-部署) | 服务器 / NAS 上跑，不用装 Python |
| [云函数](docs/deploy.md#云函数部署) | 腾讯云函数定时触发 |
| [GitHub Actions](docs/deploy.md#github-actions-定时签到) | 不用自己的机器，按 cron 定时跑一次 |

商品兑换的完整说明（计划字段、使用流程、常见问题）在 **[docs/exchange.md](docs/exchange.md)**；
推送各渠道的字段在 **[docs/push.md](docs/push.md)**。

## 关于原项目

- **跟上原项目的更新**（本仓库配了 `upstream` 远端）：

  ```bash
  git fetch upstream                          # 拉原作者的最新提交
  git log --oneline master..upstream/master   # 他有哪些改动是你还没有的（为空 = 已同步）
  git merge upstream/master                   # 合并进来
  ```

- **原作者的交流群**：群号 **1028766934**（这是**他的群，不是本仓库的**；本仓库改出来的问题请走 [Issues](https://github.com/Sting255/MiyoQian/issues)，别去麻烦群里的作者）。

  <p align="center"><img src="./assets/QQ_qrcode.jpg" alt="米游签官方交流群" width="260"></p>

- **原项目的下一步计划**：路线图在上游仓库，本 fork 不承诺跟进。

### 致谢

- 原项目：[Marchen-orz/MiyoQian](https://github.com/Marchen-orz/MiyoQian) —— 本仓库是它的衍生版，主要功能都出自原作者
- 部分思路参考：[Womsxd/MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools)、[jiarui666/mihoyo_qr_login](https://github.com/jiarui666/mihoyo_qr_login)

### 免责声明

本项目仅供学习和个人使用，请勿用于商业用途或违反米哈游、米游社相关用户协议的场景。

使用本项目产生的账号风险、数据丢失、任务失败、风控限制或其他后果均由使用者自行承担。请妥善保管账号凭证，不要将配置文件、日志文件或二维码图片公开分享。

如果你不同意以上内容，请不要使用本项目。
