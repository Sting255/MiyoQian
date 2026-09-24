# 其它运行方式

不想在本机常驻时的几种部署方式。

> 本文是 [README](../README.md) 的补充。

## 其它运行方式（Docker / 云函数 / GitHub Actions）

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

#### 容器注意事项

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

#### Actions 注意事项

- 不要把 `config.yaml`、`data/credentials.yaml`、Actions 日志截图公开给别人
- 不要在原项目仓库提交 Issue、PR 或评论时粘贴任何账号凭证
- GitHub Actions 的定时任务可能会因平台负载延迟几分钟
- 公开仓库如果长期没有活动，GitHub 可能会自动停用定时 workflow
- `config.yaml` 里的 `schedule` 配置不会影响 Actions 定时，Actions 的执行时间以 `.github/workflows/checkin.yml` 为准
- 多账号场景下账号之间会随机等 1~2 小时（防风控），所以 workflow 的超时设成了 350 分钟；只跑一个账号可以把 `timeout-minutes` 改小

---

### 云函数部署
云函数和Github Action类似，但是其在执行任务时使用的是云函数提供商的IP地址，理论上比Github Action更难风控些。缺点是可能会产生非常少量的费用（理论每个月上不到0.1元，实测上月运行了7天产生了0.02元费用被抹零不计费），适合已经用过各种云服务商的用户使用。

### 1.配置项目
参考 [快速开始](../README.md#快速开始)

注意：本项目要求 **Python 3.11+**（`pyproject.toml` 里写死了 `requires-python = ">=3.11"`），云函数请选 **Python 3.11** 运行时。如果平台只有 3.10，`uv run` 会直接拒绝，只能绕开 uv、用那个解释器手动 `pip install -r requirements.txt -t .` —— 属于不受支持的用法。
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
