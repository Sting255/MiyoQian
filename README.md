<h1>米游签</h1>

<div align="center">
  <h1 align="center">
    <img src="./miyouqian/webui/assets/myq_logo.png" width="200" alt="米游签" style="border-radius:10px">
  </h1>
  <p>一个小而美的带 Web 控制台的米游社签到工具，支持扫码登录、游戏社区签到、云游戏签到、米游币任务、每日自动执行和结果推送。</p>
  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&style=flat-square">
    <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-1E9BFA?style=flat-square">
    <img alt="Encoding" src="https://img.shields.io/badge/encoding-UTF--8-2EA44F?style=flat-square">
  </p>
  <p><sub>个人修改版，来源于 <a href="https://github.com/Marchen-orz/MiyoQian">Marchen-orz/MiyoQian</a>；本仓库改了什么见「关于这个仓库」</sub></p>
</div>

> [!IMPORTANT]
> **本仓库是 [Marchen-orz/MiyoQian](https://github.com/Marchen-orz/MiyoQian) 的衍生版本（个人修改版），不是原创项目。**
>
> - 原项目作者：**[@Marchen-orz](https://github.com/Marchen-orz)** —— 主要功能都是他写的，也由他在维护。
> - **原项目地址：<https://github.com/Marchen-orz/MiyoQian>**
>   想用原版、想提 Issue / PR、想给 Star，**都请去原仓库**。
> - 本仓库只是在他的基础上按个人需要做了一些改动（清单见下），并会持续跟踪他的更新。

## 关于这个仓库

这是个人修改版，来源是 **[Marchen-orz/MiyoQian](https://github.com/Marchen-orz/MiyoQian)**：原项目已经很完整，这份是在它基础上按自己的使用习惯改的，比原版多出来的主要是这些：

- **本地验证码识别** —— 用本机 Chrome + CDP + 一个轻量视觉模型直接解极验九宫格，不依赖第三方打码服务（网页上有「环境自检」按钮可先验证环境），打码狗作为备选。
- **出口 IP 防护** —— IP 在境外时暂停签到，避免异地登录风控；抢购场景只会在开抢前做一次**非阻塞**检查，不占用开抢时间。
- **风控节奏可调** —— 账号之间的随机间隔搬到网页上设置（单位分钟）；签到请求之间带 3~7 秒随机抖动，抢购路径不抖动（要快）。
- **推送与调度的修复** —— 兑换推送字段转义、账号崩溃/被跳过/手动停止都能在推送里看见、校验推送服务的业务返回码（不再「HTTP 200 就是成功」）、正文长度上限、每日调度不再因配置写错而静默失效。
- **测试** —— 200 个 Python 测试 + 5 个前端检查。

> 原项目作者明确希望**不要在其他平台宣传、不要大范围传播**，本仓库同样遵循：请自己用，别拿去扩散。
> 主要功能都是原作者的，用得上就去给[原项目](https://github.com/Marchen-orz/MiyoQian)点个 Star。

## 简介

米游签是一款小而美，精致便捷的米游社每日签到工具。你只需要用米游社 APP 扫码登录一次，之后就可以在本地 Web 控制台里管理账号、选择任务、设置每天自动执行签到任务。

<img src="./assets/home.png" alt="demo" style="max-width:100%;border-radius:10px">

## 功能

- 米游社 APP 扫码登录
- 多账号管理
- 游戏社区签到
- 云游戏签到（云原神、云绝区零）
- 米游币社区任务
- 米游社商品兑换
- **本地验证码识别**（不依赖第三方打码服务，见「验证码识别」一节）
- **出口 IP 防护**（IP 在境外时暂停签到，见「IP 防护」一节）
- 本地 Web 控制台
- 每日自动执行
- 执行结果推送
- 日志查看

## 支持的游戏

| 游戏 | 配置名 |
| --- | --- |
| 原神 | `genshin` |
| 崩坏：星穹铁道 | `starrail` |
| 绝区零 | `zzz` |
| 崩坏3 | `honkai3rd` |
| 未定事件簿 | `tears` |
| 崩坏学园2 | `honkai2` |

默认启用原神、崩坏：星穹铁道、绝区零。其他游戏可以在 Web 控制台或配置文件中开启。

## 云游戏签到

云游戏签到默认关闭，当前支持：

| 云游戏 | 配置名 | 状态 |
| --- | --- | --- |
| 云原神 | `genshin` | 支持 |
| 云绝区零 | `zzz` | 支持 |
| 云星穹铁道 | `starrail` | 不可选，云星穹铁道是版本更新赠送 600 分钟，不需要每日签到获取时长 |

云游戏接口使用 `x-rpc-combo_token`，不是普通米游社 cookie。云游戏 Token 按账号单独配置：先在任务配置里打开总开关，选择要执行的云游戏，再点账号行里扫码登录按钮后面的云游戏图标，填写云原神或云绝区零对应 Token。整体启用后，未选择具体云游戏或未配置对应 Token 的账号会自动跳过。

云游戏 Token 获取方法（参考 MihoyoBBSTools）：

1. 在浏览器打开对应云游戏网页并登录账号，[云原神](https://ys.mihoyo.com/cloud/#/)，[云绝区零](https://zzz.mihoyo.com/cloud-feat/#/)。
2. 打开开发者工具，切到 `Network` / `网络`。
3. 刷新页面或进入钱包/时长页面，过滤 `wallet/wallet/get`。
4. 点开返回成功的请求，在请求头里复制 `X-Rpc-Combo_token` 的值。
5. 回到米游签账号行，点击云游戏图标，把值填到对应游戏的 Token 输入框。

云原神和云绝区零的 Token 不通用，需要分别复制。这个 Token 属于敏感凭证，存储在 `data/credentials.yaml` 中：

```yaml
# config.yaml - 配置启用哪些云游戏
features:
  cloud_game_checkin: true

cloud_games:
  enabled:
    - genshin
    - zzz

# data/credentials.yaml - 存储 token 凭据
accounts:
  - name: main
    cloud_games:
      tokens:
        genshin: "云原神 x-rpc-combo_token"
        zzz: "云绝区零 x-rpc-combo_token"
```

## 米游社商品兑换

米游社商品兑换功能支持使用米游币自动兑换商品，包括定时兑换和实时兑换两种模式。

### 功能特点

- **商品浏览**：支持按游戏分区浏览可兑换商品列表
- **实时状态**：显示商品库存、兑换时间、限购情况等实时信息
- **定时兑换**：支持设置兑换计划，到点自动兑换
- **重试机制**：抢到就停，没抢到就在设定时间内继续抢（可配置）
- **多账号支持**：每个账号可配置独立的兑换计划

### 自动兑换是怎么回事

简单说：**到开卖那一刻，它会替你连着按"兑换"按钮，抢到就停，一直没抢到就坚持到你设定的秒数用完为止。**

关键是两个设置（网页「自动兑换」面板里也能改）：

| 设置 | 人话解释 |
| --- | --- |
| `retry_seconds`<br>（网页：最多抢几秒） | 开卖瞬间开始，**最多坚持多少秒**。抢到就提前结束；时间一到无论成败都停手。`0` = 只发一次请求，不重试。 |
| `retry_interval`<br>（网页：最多隔几秒发一次） | 两次请求之间**最多隔多久**。实际是在 **0 ~ 这个数之间随机**（填 `0.3` 就是 0~0.3 秒随机），加随机是为了别让节奏太机械。填 `0` = 完全不等待。 |

网页上会根据你填的数字实时估算大概发几次请求，例如「最多抢 5.5 秒 / 最多隔 0.3 秒」≈ 16 次（最少 11 次、最多 27 次）。

哪些行为是安全的（不会重复下单）：

- **同一个「商品 + 开卖时间」只会自动抢一次**，失败也不会自动再来一轮（想重来就把计划的开卖时间改一下，或手动点「立即兑换」）。
- **抢到之后计划会自动关闭**，不会对着同一个商品反复下单。
- 「启用商品自动兑换」这个开关**只管到点自动那条路**；商品卡片上的「立即兑换」不受它影响，手动随时能用。

### 配置说明

在 `config.yaml` 中配置商品兑换功能：

```yaml
shop_exchange:
  enable: true          # 是否开启"到点自动抢"（关着也能手动兑换）
  retry_seconds: 20     # 到点后最多抢几秒（0 = 只发一次）
  retry_interval: 0.4   # 每两次之间最多隔几秒（实际 0~这个数 之间随机；0 = 不等）
  push: true            # 兑换结束后是否推送结果
  plans: []             # 兑换计划列表
```

### 兑换计划配置

每个兑换计划包含以下字段：

| 字段 | 说明 | 示例 |
| --- | --- | --- |
| `goods_id` | 商品 ID | "12345" |
| `goods_name` | 商品名称（可选，用于显示） | "原神礼包" |
| `account` | 执行兑换的账号名称 | "main" |
| `address_id` | 收货地址 ID（实物商品必需） | "67890" |
| `uid` | 游戏 UID（游戏内商品必需） | "100000001" |
| `region` | 游戏区服（游戏内商品必需） | "cn_gf01" |
| `game_biz` | 游戏业务标识（游戏内商品必需） | "hk4e_cn" |
| `exchange_at` | 兑换时间戳（秒） | 1700000000 |
| `enable` | 是否启用该计划 | true |
| `auto` | 是否自动执行 | true |
| `device_fp` | 设备指纹（自动获取） | "xxx" |

完整配置示例：

```yaml
shop_exchange:
  enable: true
  retry_seconds: 20
  retry_interval: 0.4
  plans:
    - goods_id: "12345"
      goods_name: "原神礼包"
      account: "main"
      address_id: "67890"
      exchange_at: 1700000000
      enable: true
      auto: true
      device_fp: ""
```

### 使用方式

#### Web 控制台操作

1. **商品浏览**：在 Web 控制台进入"商品兑换"页面
2. **选择商品**：浏览商品列表，选择要兑换的商品
3. **查看详情**：点击商品查看详细信息，包括库存、兑换时间等
4. **添加计划**：点击"添加兑换计划"，填写相关信息
5. **设置时间**：设置兑换时间（支持定时兑换）
6. **立即兑换**：对于已上架的商品，可以立即兑换

#### 商品类型说明

- **实物商品**：需要填写收货地址
- **游戏内商品**：需要填写游戏 UID、区服信息
- **通用商品**：可能不需要额外信息

### 兑换流程

1. **自动获取 device_fp**：系统会自动获取设备指纹
2. **时间校准**：定时兑换前会自动校准服务器时间
3. **精确等待**：兑换前 180 秒进入精确等待模式
4. **重试机制**：在配置的重试时长内持续尝试兑换
5. **结果通知**：兑换完成后通过配置的推送渠道通知

### 注意事项

- 兑换功能需要账号已登录且凭证有效
- 实物商品需要提前在米游社配置收货地址
- 游戏内商品需要确保角色信息正确
- 定时兑换时间建议设置在商品上架时间点
- 重试时长和间隔可根据网络情况调整
- 兑换结果会记录在日志中，失败会显示原因

### 常见问题

**Q: 如何获取商品 ID？**  
A: 在 Web 控制台的商品列表中可以查看所有商品的 ID。

**Q: 如何获取收货地址 ID？**  
A: 在 Web 控制台添加兑换计划时，会自动加载账号的收货地址列表。

**Q: 游戏内商品的 UID 和区服如何填写？**  
A: 在 Web 控制台添加兑换计划时，会自动加载账号的角色信息。

**Q: 兑换失败怎么办？**  
A: 检查日志中的错误信息，常见原因包括：库存不足、时间未到、账号信息错误等。

**Q: 可以同时添加多个兑换计划吗？**  
A: 可以，每个账号可以添加多个兑换计划，系统会按时间顺序执行。

## 米游币任务

米游币任务默认关闭，需要时手动开启。可执行的任务包括：

- 社区签到
- 看帖
- 点赞
- 分享
- 点赞后自动取消点赞

米游币任务比游戏社区签到更容易遇到验证码或风控。第一次使用建议先只开启游戏社区签到，确认稳定后再开启米游币任务。

## 部署流程

### Docker 部署（推荐）

适合部署在服务器或 NAS 上，无需手动安装 Python 和 uv。

#### 1. 获取项目文件

```bash
git clone <你的仓库地址>
cd MiyoQian
```

#### 2. 构建并启动

```bash
# 进入 docker 目录
cd docker
docker compose up -d --build
```

启动完成后，浏览器打开 **http://localhost:5890** 即可。

> 容器内服务已默认监听 `0.0.0.0`（Docker 必需），宿主机通过 `localhost:5890` 访问即可。

#### 3. 常用命令

```bash
cd docker

# 查看日志
docker compose logs -f

# 停止并删除容器
docker compose down

# 代码更新后重新构建
docker compose up -d --build
```

#### 4. 数据持久化

`docker-compose.yml` 默认使用 Docker 命名卷保存容器专用数据，不再和项目根目录的 `config.yaml`、`data/`、`logs/` 共用：

| Docker 卷 | 容器路径 | 说明 |
| --- | --- | --- |
| `docker_miyouqian_state` | `/app/state` | Docker 专用配置、登录凭证和运行日志 |

容器重建后数据不会丢失。只有执行 `docker compose down -v` 或手动删除该 Docker 卷时，Docker 环境内的配置和登录凭证才会被删除。

#### 注意事项

- 首次使用需要先进容器完成扫码登录：`docker compose exec miyouqian sh`
- 容器内时区默认为 `Asia/Shanghai`，如需修改可在 `docker-compose.yml` 中调整 `TZ` 环境变量
- 如需修改容器内监听端口，同时修改 `docker-compose.yml` 的 `ports` 和 Docker 启动命令中的 `--port`

### GitHub Actions 定时签到（无需服务器）

适合不想长期运行电脑、服务器或 NAS 的用户。Actions 会按 `.github/workflows/checkin.yml` 里的时间自动执行一次 `python main.py run`。

> ⚠️ 想用 Actions 的话，**不要直接 Fork**，请点 **Use this template** → **Create a new repository**
> 创建到你自己的账号下，否则运行时长会算到上游仓库，可能连累它被封禁。
>
> （本仓库本身就是上游的 fork，**fork 上的 Actions 默认是关闭的**；要用得自己去 `Actions`
> 页面手动启用，时长算在你自己的额度里。）

> ⚠️ GitHub Actions 只负责定时触发一次性签到，不会启动 Web 控制台。首次扫码登录和配置调整建议先在本地或 Docker 环境完成。

#### 1. 本地生成配置和凭证

先按「本地部署」或「Docker 部署」完成一次扫码登录，并确认手动执行签到正常。完成后项目目录里会有：

| 文件 | 说明 |
| --- | --- |
| `config.yaml` | 任务配置，可以提交仓库 |
| `data/credentials.yaml` | 登录凭证（含账号信息），不要提交仓库 |

#### 2. 创建项目并启用 Actions

点仓库右上角的 `Use this template` → `Create a new repository`，把项目创建到你自己的 GitHub 账号下。

进入你创建的新仓库后，打开 `Actions` 页面。如果页面提示 workflow 被禁用，点一下启用。

开启 Actions 后，无论有没有配置 Secrets 都会每天执行一次。**不要在原项目仓库里配置你的账号凭证**，也不要把凭证发给项目作者。

#### 3. 添加 GitHub Secrets

进入你创建的新仓库：`Settings` → `Secrets and variables` → `Actions` → `New repository secret`。

添加两个 Secret：

| Secret 名称 | 内容 |
| --- | --- |
| `MIYOUQIAN_CONFIG` | 复制 `config.yaml` 的完整内容 |
| `MIYOUQIAN_CREDENTIALS` | 复制 `data/credentials.yaml` 的完整内容 |

粘贴时保留 YAML 原本的换行和缩进。凭证过期、账号变化或推送配置变化后，重新复制最新文件内容覆盖对应 Secret 即可。

#### 4. 修改定时执行时间

默认每天北京时间 `09:20` 执行：

```yaml
schedule:
  - cron: "20 9 * * *"
    timezone: "Asia/Shanghai"
```

如果想改成每天北京时间 `18:30`，改为：

```yaml
schedule:
  - cron: "30 18 * * *"
    timezone: "Asia/Shanghai"
```

`cron` 的 5 个字段分别是：

```text
分钟 小时 日期 月份 星期
```

不建议设置在整点，例如 `0 9 * * *`，整点附近 GitHub Actions 排队更容易延迟。定时任务只会在默认分支上的 workflow 生效。

#### 5. 手动执行一次

进入你创建的仓库的 `Actions` 页面，选择 `米游签定时签到`，点击 `Run workflow` → `Run workflow`。

等 1~2 分钟，在日志底部可以看到任务摘要（执行结果也会按 `config.yaml` 里的推送配置发出）。

#### 注意事项

- 不要把 `config.yaml`、`data/credentials.yaml`、Actions 日志截图公开给别人
- 不要在原项目仓库提交 Issue、PR 或评论时粘贴任何账号凭证
- GitHub Actions 的定时任务可能会因平台负载延迟几分钟
- 公开仓库如果长期没有活动，GitHub 可能会自动停用定时 workflow
- `config.yaml` 里的 `schedule` 配置不会影响 Actions 定时，Actions 的执行时间以 `.github/workflows/checkin.yml` 为准
- 多账号场景下账号之间会随机等 1~2 小时（防风控），所以 workflow 的超时设成了 350 分钟；只跑一个账号可以把 `timeout-minutes` 改小

---

### 本地部署

下面以本地部署为例。项目推荐使用 `uv` 管理 Python 环境和依赖。Windows 用户可以直接使用 PowerShell；Linux 或 macOS 用户把对应命令换成下方给出的 shell 命令即可。

### 1. 安装 uv

Windows PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Linux 或 macOS：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后，重新打开一个终端，检查 uv 是否可用：

```powershell
uv --version
```

看到版本号后再继续下一步。uv 会为项目创建虚拟环境，也可以在需要时自动准备 Python 版本。

### 2. 获取项目文件

如果你下载的是压缩包，请先解压，然后进入解压后的项目目录。

如果你使用 Git，可以执行：

```powershell
git clone <你的仓库地址>
cd 米游社签到
```

后续所有命令都需要在项目目录中执行，也就是能看到 `main.py`、`pyproject.toml` 和 `uv.lock` 的目录。

### 3. 创建虚拟环境并安装依赖

在项目目录执行：

```powershell
uv venv --python 3.11
uv sync
```

这会在项目目录下创建 `.venv`，并安装项目运行所需依赖。日常使用时不需要手动激活虚拟环境，后续命令直接通过 `uv run` 执行即可。

如果下载依赖很慢，可以临时使用镜像源：

```powershell
uv sync --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 启动 Web 控制台

```powershell
uv run python main.py
```

启动成功后，终端会显示访问地址，通常是：

```text
http://127.0.0.1:5890
```

如果端口被占用，程序会自动切换到后续端口。请以终端显示的实际地址为准。

### 5. 完成首次配置

打开 Web 控制台后，按顺序完成：

1. 添加账号
2. 使用米游社 APP 扫码登录
3. 选择要执行的游戏社区签到任务
4. 点击“立即执行”测试一次
5. 测试正常后再开启每日调度和推送

### 6. 保持程序运行

每日自动执行依赖当前程序保持运行。关闭终端、电脑关机或休眠后，自动任务不会继续执行。


---

### 云函数部署
云函数和Github Action类似，但是其在执行任务时使用的是云函数提供商的IP地址，理论上比Github Action更难风控些。缺点是可能会产生非常少量的费用（理论每个月上不到0.1元，实测上月运行了7天产生了0.02元费用被抹零不计费），适合已经用过各种云服务商的用户使用。

### 1.配置项目
参考 [本地部署1-5](#本地部署)

注意：在配置虚拟环境时应使用`uv venv --python 3.10`,因为腾讯云暂时还没有python3.11镜像(如果忘了其实也没关系，因为经过测试3.11安装的依赖在3.10上也能跑，但是最好不要这么做qwq)。
### 2.打包项目
```powershell
uv run python -m pip install -r requirements.txt -t .
```
这一步将会把项目需要的依赖直接安装到项目根目录，安装完成后，将项目压缩为.zip备用

### 3.上传项目并配置自动运行
这里以腾讯云为例（阿里云等其它云服务提供商也有类似服务，方法类似）
#### 3.1 登录并创建云函数
打开 [腾讯云函数](https://console.cloud.tencent.com/scf/list)，登录后依次点击

>新建 > 从头开始 
> 
>函数类型:事件函数
> 
>**运行环境:Python3.10**
> 
>时区:Asia/Shanghai
>
>提交方法：本地上传zip包
> 
>**执行方法：index.main_handler**
>
> 函数代码:上传[第二步](#2打包项目)压缩的压缩包
> 
> 高级设置：
> 
> 初始化超时时间:30
> 
> 执行超时时间:600

勾选同意协议后完成创建
#### 3.2 配置触发器
创建完成后点击你刚刚创建的云函数，选择触发管理>创建触发器
>触发周期：自定义触发周期（推荐）
> 
> Cron表达式:
> 
> 注：这里的Cron表达式和上文中的规则不一样，这里需要7个字段，分别是 
> 
> 秒 分钟 小时 日 月 星期 年
> 
> 示例：
> 
> 每天早上7点30触发：0 30 7 * * * *
> 
> 详细参照[定时触发器说明](https://cloud.tencent.com/document/product/583/9708)

---

## 快速开始

推荐使用 Web 控制台完成所有日常操作。

### 1. 启动

在项目目录运行：

```powershell
uv run python main.py
```

启动成功后，终端会显示 Web 控制台地址，通常是：

```text
http://127.0.0.1:5890
```

如果 `5890` 端口被占用，程序会自动尝试后续端口。请以终端里显示的实际地址为准。

### 2. 添加账号

打开 Web 控制台后：

1. 在“账号”区域点击“添加”
2. 可以先把账号名改成 `main`、`小号1` 等方便识别的名字
3. 点击账号卡片里的“登录”
4. 使用米游社 APP 扫码

扫码路径：

```text
米游社 APP -> 我的 -> 左上角扫一扫
```

登录成功后，账号卡片会显示 UID。

### 3. 选择任务

在“任务配置”里选择你想执行的内容：

- 游戏社区签到
- 具体游戏
- 云游戏签到
- 米游币任务
- 社区签到、看帖、点赞、分享

第一次使用建议只开启“游戏社区签到”，先跑通一次。

### 4. 手动测试

点击右上角“立即执行”。执行过程中可以在右侧日志区域查看结果。

如果提示“今日已签到”，说明账号和任务配置已经正常。

### 5. 开启每日自动执行

确认手动执行正常后，再展开“每日调度”：

1. 勾选“启用每日自动执行”
2. 设置执行时间
3. 设置随机波动分钟
4. 按需选择“启动后执行一次”

示例：执行时间为 `09:00`，随机波动为 `45`，表示每天 09:00 到 09:45 之间随机执行一次。

自动执行要求程序保持运行。关闭终端、电脑关机或休眠后，任务不会继续执行，所以推荐服务器上运行。

## IP 防护（境外 IP 时暂停签到）

开着 VPN / 代理签到，出口 IP 会跑到境外或异地，很容易触发米游社风控。开启 IP 防护后，每个账号签到前都会先查一次公网出口 IP：

- 出口 IP 在中国大陆 → 正常签到
- 出口 IP 不在中国大陆 → **暂停签到**，每隔一段时间复查，IP 回到大陆后自动继续
- 超过最长等待时间仍未恢复 → 放弃本次，并推送通知

```yaml
ip_guard:
  enable: true             # 关闭后不再检查
  check_interval: 300      # 复查间隔（秒）
  max_wait: 7200           # 最长等待（秒），0 = 一直等
  notify: true             # 暂停 / 恢复 / 放弃时推送通知
  on_error: allow          # 查询失败时放行(allow) 还是按境外处理(block)
  endpoints: []            # 可选：自定义查询接口，留空用内置接口
```

说明：

| 设置 | 说明 |
| --- | --- |
| `check_interval` | 暂停后每隔多久复查一次出口 IP，最小 30 秒 |
| `max_wait` | 累计等待超过这个时长就放弃本次签到，`0` 表示不限时长一直等 |
| `notify` | 暂停、恢复、放弃时各推送一次通知（仍需先配置推送通道） |
| `on_error` | 查询接口全部不可用时怎么办：`allow` 放行继续签到，`block` 当作境外处理 |
| `endpoints` | 自定义查询接口，只要响应里包含 IP 即可，最多 5 个 |

判定依据：**同时探测两条链路**，任意一条出口在境外都会暂停。

| 链路 | 探测接口 | 说明 |
| --- | --- | --- |
| 国内直连 | ipip.net / 3322 / 淘宝 | 代理规则里多是中国大陆直连 |
| 境外流量 | ipify / ipinfo / myip | 米游社是境外服务，走的是这条路 |

为什么要分开查：**分流模式（规则模式）的代理下，国内直连、国外走代理，两边看到的出口 IP 不一样**。如果只查国内接口，会看到江苏的 IP 以为一切正常，但米游社实际是从香港出去的，一样会触发风控。分开探测后这种「假正常」能被识别出来。

**中国香港、中国澳门、中国台湾的网络出口与海外一样不算中国大陆**，同样会暂停。

Web 控制台的「IP 防护」面板可以开关、调整间隔、点「立即检测出口 IP」当场看两条链路的结果。

> 注意：判定依赖第三方查询接口。接口全部不可用且 `on_error: allow` 时会放行，不会因此卡住签到。

## Web 控制台说明

Web 控制台主要分为几块：

| 区域 | 用途 |
| --- | --- |
| 顶部状态 | 查看账号数量、自动调度状态、下次执行时间、最近结果 |
| 账号 | 添加账号、扫码登录、刷新凭证、删除账号、设置该账号的独立任务 |
| 任务配置 | 开关游戏社区签到、云游戏签到和米游币任务（所有账号的默认值） |
| 设备指纹 | 查看和更换机型、设备 ID、设备 FP |
| 每日调度 | 设置每天什么时候自动运行 |
| 推送通道 | 设置任务完成后的通知方式 |
| IP 防护 | 境外 IP 时暂停签到，恢复后自动继续 |
| 日志 | 查看本次启动后的运行记录 |

### 停止正在执行的任务

点顶部的「停止」按钮可以中断正在跑的签到：

- 如果正在**账号间等待**（多账号之间默认随机等 1-2 小时，可在「每日调度」面板里改），会立刻中断，不再跑剩下的账号
- 如果正在**等 IP 恢复**（IP 防护暂停中），同样会立刻中断
- 如果正在跑某个账号的签到，会等这个账号当前的任务结束后停止，不会硬杀进程

停止后仍会推送一条结果，标题会说明是手动停止的。

### 每个账号单独推送

推送通道里可以勾选「每跑完一个账号就推送一次」。多账号场景下，第一个账号跑完就发一条，不用等全部账号跑完（账号间隔默认长达 1-2 小时）：

```
【大号】游戏 6/6 · 米游币 +40
【小号】游戏 2/2 · 米游币 +40
```

不勾选时只在整轮任务结束后推送一条总结。

> ⚠️ 它和「只在失败时推送」(`error_only`) 是同一个开关在管：开着 `error_only` 时，
> 逐账号的**成功**推送也会被跳过，只有失败的账号会立刻推一条，其余要等最后的总结推送。

#### 命令行的退出码

`python main.py run` 现在会**按任务结果返回退出码**（成功 `0`、失败 `1`），
cron、CI、脚本可以直接用它判断要不要告警。注意 GitHub Actions 的 workflow 里那句
`uv run python main.py run` 带了 `set -e`，所以签到失败会让这次 Action 标记为失败
（后面的「输出任务摘要」步骤仍会执行，日志照常能看到）。
Windows 计划任务里也可以用它决定是否重试。

日志区域只显示本次 Web 服务启动后的记录。历史日志会保存在 `logs/miyouqian.log`。

## 命令行用法

如果你不想使用 Web 控制台，也可以用命令行。

生成配置：

```powershell
uv run python main.py init
```

扫码登录：

```powershell
uv run python main.py login --account main
```

执行全部任务：

```powershell
uv run python main.py run
```

只执行指定账号：

```powershell
uv run python main.py run --account main
```

只执行游戏社区签到和云游戏签到：

```powershell
uv run python main.py run --games-only
```

只执行米游币任务：

```powershell
uv run python main.py run --bbs-only
```

只执行指定游戏：

```powershell
uv run python main.py run --game genshin --game starrail
```

启动 Web 控制台并指定端口：

```powershell
uv run python main.py serve --port 5891
```

`--host` 和 `--port` 参数会覆盖配置文件中的设置。不指定时读取 `config.yaml` 中的 `web.host` 和 `web.port`。

查看当前配置摘要：

```powershell
uv run python main.py show
```

## 常用设置

大多数设置都可以在 Web 控制台里完成。只有在需要批量修改、迁移配置或高级调整时，才建议手动编辑 `config.yaml`。

请使用 UTF-8 编码保存配置文件，否则中文可能乱码。

### 文件位置

默认会生成或使用这些文件：

```text
config.yaml              # 账号名称、任务开关、调度、推送等普通配置
data/credentials.yaml    # 登录凭证
logs/miyouqian.log       # 历史日志
qrcode.png               # 命令行扫码登录时生成的二维码图片
```

开发时不要把这些文件上传到公开仓库，尤其是 `data/credentials.yaml`。

### 账号

Web 控制台添加账号后，会在配置中保存账号名。扫码登录成功后，登录凭证会单独保存到 `data/credentials.yaml`。

通常不需要手动填写 cookie 或 stoken。

### 每账号独立任务配置

默认情况下所有账号共用一套任务配置。如果不同账号玩的游戏不一样，可以给单个账号设置独立任务：

1. 在账号卡片右侧点击「任务」按钮（滑块图标）
2. 勾选「为该账号单独设置任务」
3. 勾选这个账号要跑的游戏、云游戏和米游币任务

取消勾选即可让该账号恢复跟随全局配置。配置文件里的结构如下：

```yaml
accounts:
  - name: 大号
    tasks:
      features:
        game_checkin: true
        cloud_game_checkin: false
        bbs_tasks: false
      games:
        enabled:
          - genshin
          - starrail
      bbs:
        checkin: true
  - name: 小号
```

没有 `tasks` 字段的账号，或 `tasks` 里没写的部分，都会自动沿用上面的全局配置。执行日志里出现「该账号使用独立任务配置」时，说明该账号正在使用自己的配置。

### 设备指纹

请求头里的机型、设备 ID（`x-rpc-device_id`）和设备指纹（`x-rpc-device_fp`）会参与米游社的风控判断。同一套指纹长期高频使用容易被标记，遇到验证码或风控时可以换一套。

- 在 Web 控制台「设备指纹」面板里可以直接看到当前机型、设备 ID 和设备 FP
- 点「随机换一个」会随机挑一个机型并重新生成设备 ID 和 FP
- 点「使用该机型」则按下拉框里选中的机型生成新指纹
- 内置了 39 个常见安卓机型预设，也可以在配置文件的 `device.presets` 里自己增删

换指纹不影响已经登录的凭证，但需要重启程序后新的指纹才会在下次执行时生效。

### 游戏社区签到

如果你想手动指定游戏，可以编辑：

```yaml
games:
  enabled:
    - genshin
    - starrail
    - zzz
```

如果某个游戏里有多个角色，但你想跳过其中一个角色，可以把角色 UID 加到黑名单：

```yaml
games:
  black_list:
    genshin:
      - "100000001"
```

### 云游戏签到

开启云游戏签到：

```yaml
features:
  cloud_game_checkin: true
```

选择要执行的云游戏：

```yaml
cloud_games:
  enabled:
    - genshin
    - zzz
```

在 `data/credentials.yaml` 中为每个账号填写云游戏 Token：

```yaml
accounts:
  - name: main
    cloud_games:
      tokens:
        genshin: "云原神 x-rpc-combo_token"
        zzz: ""
```

云游戏 Token 获取方法：

1. 在浏览器打开对应云游戏网页并登录账号，例如云原神。
2. 打开开发者工具，切到 `Network` / `网络`。
3. 刷新页面或进入钱包/时长页面，过滤 `wallet/wallet/get`。
4. 点开返回成功的请求，在请求头里复制 `X-Rpc-Combo_token` 的值。
5. 回到米游签账号行，点击云游戏图标，把值填到对应游戏的 Token 输入框。

云原神和云绝区零的 Token 不通用，需要分别复制。云星穹铁道会在 Web 控制台中显示为不可选状态，原因是云星穹铁道是版本更新赠送 600 分钟，不需要每日签到获取时长。未选择具体云游戏或未配置对应 Token 的账号会在整体启用后跳过。

### 米游币任务

开启米游币任务：

```yaml
features:
  bbs_tasks: true
```

常用选项：

```yaml
bbs:
  forums:
    - 5
    - 2
  checkin: true
  cancel_like: true
  delay_seconds:
    - 1
    - 3
```

> `read` / `like` / `share` 三个开关目前**不生效**：程序里会强制按 `false` 处理
> （米游社已经改了米游币获取规则）。写上它们不会有任何效果。

社区 ID：

| ID | 社区 |
| --- | --- |
| `1` | 崩坏3 |
| `2` | 原神 |
| `3` | 崩坏2 |
| `4` | 未定事件簿 |
| `5` | 大别野 |
| `6` | 崩坏：星穹铁道 |
| `8` | 绝区零 |

### 验证码识别

项目默认不会自动处理验证码。验证码识别按渠道配置，目前有两个渠道：本地识别（免费）和打码狗（付费）。

```yaml
captcha:
  max_retries: 3
  channels:
    - provider: local      # 本地识别，免费
      enable: false
      headless: true       # 用无头浏览器，改成 false 可以看到浏览器窗口
      max_attempts: 5      # 单次验证码最多尝试几次
      model_path: ""       # 模型路径，留空则用 data/models/
    - provider: damagou    # 打码狗，按次收费
      enable: false
      userkey: ""
      timeout: 60
```

说明：

| 设置 | 说明 |
| --- | --- |
| `max_retries` | 每次触发验证码后最多重新获取并识别的次数 |
| `channels[].provider` | 识别渠道，`local` 或 `damagou` |
| `channels[].enable` | 是否启用该渠道 |

#### 本地识别（免费）

米游社社区签到用的是极验三代「九宫格点选」验证码：给一个小图标，让你在 3×3 的图片里选出包含这个物体的格子。

本地识别的做法是：

1. 用本机 Chrome / Edge 加载极验自己的 JS，把验证码渲染出来——**校验数据由极验前端自己生成**，不去逆向加密参数，所以抗改版能力最强；
2. 用视觉模型（CLIP 图像编码器）把提示图标和九个格子做相似度比较，挑出应当点选的格子；
3. 用浏览器自动化点击，拿到 `geetest_validate` 后提交给米游社。

需要满足：

- 本机装有 Chrome 或 Edge（没有的话自动识别会失败并写日志）
- 首次使用会自动下载视觉模型（约 85MB，走国内镜像）到 `data/models/`，需要联网
- 因为要驱动浏览器，每次识别大概需要十几秒

单次识别不一定百分百正确，所以触发验证码后会重新获取验证码并重试，最多 `max_retries × max_attempts` 次。

#### 打码狗（付费）

填上 `userkey` 即可，按次计费。`type` 默认不需要设置；识别困难时可以给该渠道补 `type: "1006"`，会增加积分消耗。

识别、校验或提交失败时，会重新获取验证码并重试，最多执行 `max_retries` 次。

### 每日调度

```yaml
schedule:
  enable: true
  time: "09:00"
  jitter_minutes: 45
  run_on_start: false
```

含义：

| 设置 | 说明 |
| --- | --- |
| `enable` | 是否开启每日自动执行 |
| `time` | 每天的基准执行时间 |
| `jitter_minutes` | 随机延后分钟数 |
| `run_on_start` | 启动 Web 服务后是否立即执行一次 |

### 账号之间的等待

多账号时，跑完一个账号会先随机等一会儿再跑下一个，让它们看起来像不同时段各自操作的真人，
降低「同一 IP 短时间内多账号」被风控的概率。**在网页「每日调度」面板里可以直接改，不用编辑配置文件。**

```yaml
account_gap:
  enable: true          # false = 所有账号连着跑，不等待
  min_minutes: 60       # 最短等待（分钟）
  max_minutes: 120      # 最长等待（分钟）
```

| 设置 | 说明 |
| --- | --- |
| `enable` | 关掉就等于所有账号连着跑 |
| `min_minutes` / `max_minutes` | 每次在这两个值之间随机取一个；两个都填 `0` 也等于不等待 |

- 上限是 1440 分钟（24 小时）：再长就会把后面的账号推到第二天的定时任务上去。
- `min` 比 `max` 大也没关系，会自动交换。
- 等待期间点网页上的「停止」可以立刻中断，不再跑剩下的账号。
- 只有 1 个账号时不会等待。

### 网络访问与密码

Web 控制台默认只监听本机地址 `127.0.0.1`，只能在本机浏览器中访问。

如果需要从局域网或外网访问（例如部署在服务器上），可以在配置文件中修改监听地址：

```yaml
web:
  host: "0.0.0.0"   # 监听地址，设为 0.0.0.0 允许外部访问
  port: 5890        # 监听端口
  password: ""      # 访问密码（见下方说明）
```

**密码说明：**

- `host` 为 `127.0.0.1` 或 `localhost` 时，不需要密码，直接访问
- `host` 为 `0.0.0.0` 或其他非本机地址时，必须设置密码才能使用
- 首次访问会显示密码设置页面，输入后自动保存（存储为哈希值）
- 也可以在配置文件中直接填写明文密码，启动时会自动转换为哈希
- 服务重启后需要重新输入密码

| 设置 | 说明 |
| --- | --- |
| `host` | 监听地址，`127.0.0.1` 仅本机，`0.0.0.0` 允许外部 |
| `port` | 监听端口，默认 `5890` |
| `password` | 访问密码，留空首次通过页面设置，也可直接填写明文 |

## 推送设置

推送可以在 Web 控制台中配置。开启后，任务结束时会发送结果通知。

如果只想失败时通知，勾选“仅失败时推送”，或设置：

```yaml
push:
  error_only: true
```

### pushplus

需要填写：

- Token
- 群组编码，可留空

示例：

```yaml
push:
  channels:
    - provider: pushplus
      enable: true
      token: "你的 token"
      topic: ""
```

### Telegram

需要填写：

- Bot Token
- Chat ID

示例：

```yaml
push:
  channels:
    - provider: telegram
      enable: true
      token: "bot token"
      chat_id: "chat id"
```

### 钉钉机器人

需要填写：

- Webhook
- 加签 Secret，可留空

示例：

```yaml
push:
  channels:
    - provider: dingrobot
      enable: true
      webhook: "https://oapi.dingtalk.com/robot/send?access_token=..."
      secret: "SEC..."
```

### 飞书机器人

需要填写：

- Webhook

示例：

```yaml
push:
  channels:
    - provider: feishubot
      enable: true
      webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

### 邮箱

需要填写：

- SMTP 服务器
- SMTP 端口
- 邮箱账号
- 邮箱授权码
- 发件人
- 收件人

示例：

```yaml
push:
  channels:
    - provider: email
      enable: true
      smtp_host: "smtp.example.com"
      smtp_port: 465
      smtp_user: "name@example.com"
      smtp_password: "邮箱授权码"
      mail_from: "name@example.com"
      mail_to: "target@example.com"
      smtp_ssl: true
```

多个收件人通常可以用英文逗号分隔。

## 推荐使用流程

第一次使用建议这样做：

1. 安装 uv
2. 创建虚拟环境并安装依赖
3. 运行 `uv run python main.py`
4. 打开 Web 控制台
5. 添加账号并扫码登录
6. 只开启游戏社区签到
7. 点击“立即执行”
8. 确认日志正常
9. 开启每日调度
10. 按需开启推送
11. 最后再考虑开启米游币任务

## FAQ

### 扫码登录后凭证保存在哪里？

默认保存在 `data/credentials.yaml`。普通设置保存在 `config.yaml`。

### 为什么 `config.yaml` 里看不到 cookie？

登录凭证会单独保存，避免普通配置文件里直接暴露敏感信息。

### Web 控制台打不开怎么办？

先看终端里显示的实际地址。默认端口是 `5890`，可以在配置文件 `web.port` 中修改，如果被占用，程序会自动切换到后续端口。

也可以手动指定端口：

```powershell
uv run python main.py serve --port 5891
```

### 自动调度没有执行？

请检查：

- 程序是否一直运行
- “每日调度”是否已开启
- 电脑是否休眠或关机
- 当前时间是否已经过了今天的执行窗口
- 日志里是否显示了下次执行时间

### 签到提示首次绑定怎么办？

部分游戏第一次绑定签到活动时，需要先在米游社或活动页面手动签到一次。手动完成后，后续再交给米游签执行。

### 遇到验证码怎么办？

默认不会自动处理验证码。遇到验证码时会跳过该项并写入日志。可以稍后手动签到，或降低米游币任务使用频率。

如果你有打码狗 `userkey`，可以在 Web 控制台开启验证码识别。项目只负责调用打码狗并按游戏社区签到、米游币社区签到两种不同方式提交结果。

### 米游币任务失败，但游戏社区签到正常？

这是可能的。米游币任务更容易受到验证码、风控和任务状态影响。建议先确保游戏社区签到稳定，再开启米游币任务。

### 可以添加多个账号吗？

可以。每个账号都需要分别扫码登录。不要重复添加同一个 UID。

### 可以放到服务器运行吗？

本项目推荐放到服务器运行，但要注意：

- 扫码登录时需要能看到二维码
- 如果需要外部访问，将 `web.host` 设为 `0.0.0.0`，并设置访问密码
- 也可以用 `--host 0.0.0.0` 启动，但密码仍需在配置文件或首次访问时设置
- 服务器时间会影响每日调度时间

## 原项目的下一步计划

下面这些是**上游项目**的路线图（本 fork 不承诺跟进，仅作参考）：

| 完成 | 计划 | 完成时间 |
| --- | --- | --- |
| [ ] | 补充更多游戏渠道签到 | -- |
| [x] | 新增米游社其他功能，例如米游币兑换功能 | 2025-01 |
| [ ] | 补充更多推送渠道 | -- |
| [ ] | 优化签到流程，降低风控风险 | -- |
| [ ] | 增加配置导入、导出功能 | -- |
| [ ] | 完善产品文档 | -- |
| [ ] | 持续适配米游社规则变化，尽量保持工具可用 | -- |
| [ ] | ... | -- |

## 开发与测试

项目自带一套回归测试，改代码前先跑一遍：

```bash
uv run python -m unittest discover -s tests -t .
```

- 测试全部在 `.tmp-tests/` 的临时目录里读写配置，**不会碰你的 `config.yaml` 和 `data/credentials.yaml`**。
  （`run_tasks()` / 保存配置都会写盘，所以这条是硬性要求。）
- 只用标准库 `unittest`，不需要额外安装依赖。
- `tests/*.test.mjs` 是前端检查，需要本机有 `node`；它会直接从 `app.js` 里抽出真实函数来跑，
  没有 node 时这部分会自动跳过。
- 新增配置项或改前端时，记得同步补一个测试：历史上「保存配置把凭证冲掉」「筛选后兑错商品」
  这类问题都是靠测试才守住不复发的。

## 注意事项

- 请妥善保管 `data/credentials.yaml`
- 不要公开上传配置文件、日志文件或二维码图片
- `GET /api/config` 会把账号 cookie/stoken、推送密钥等**替换成占位符**后再回给浏览器，
  保存时会自动还原，所以正常使用不需要关心；但这也意味着**别把抓包内容当配置备份**。
- 遇到验证码时需要手动处理
- 如果米游社规则变化，可能会出现签到失败，需要等待项目更新
- 使用自动化工具存在账号风控风险，请低频、保守使用

## 跟上原项目的更新

本仓库把原项目配成了 `upstream` 远端，随时可以看原作者改了什么：

```bash
git fetch upstream                          # 拉取原作者的最新提交
git log --oneline master..upstream/master   # 他有哪些改动是你还没有的（输出为空 = 已同步）
git diff --stat master upstream/master      # 看他具体改了哪些文件
git merge upstream/master                   # 合并进来
```

## 原作者的交流群

下面是**原项目作者的官方交流群**（群号 **1028766934**）—— 想参与开发、或者使用中遇到问题，可以去群里问。

> ⚠️ 这是**他的群，不是本仓库的**；群里讨论的主要也是原项目。本仓库的改动如果出了问题，请在[本仓库的 Issues](https://github.com/Sting255/MiyoQian/issues) 里提，别去麻烦群里的作者。

<p align="center">
  <img src="./assets/QQ_qrcode.jpg" alt="米游签官方交流群" width="300">
</p>

## 致谢

- 原项目：[Marchen-orz/MiyoQian](https://github.com/Marchen-orz/MiyoQian) —— 本仓库是它的 fork，主要功能都出自原作者
- 部分功能思路参考：
  - [Womsxd/MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools)
  - [jiarui666/mihoyo_qr_login](https://github.com/jiarui666/mihoyo_qr_login)

## 免责声明

本项目仅供学习和个人使用，请勿用于商业用途或违反米哈游、米游社相关用户协议的场景。

使用本项目产生的账号风险、数据丢失、任务失败、风控限制或其他后果均由使用者自行承担。请妥善保管账号凭证，不要将配置文件、日志文件或二维码图片公开分享。

如果你不同意以上内容，请不要使用本项目。
