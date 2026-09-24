# 配置说明

`config.yaml` 里所有能改的东西。改完不用重启，[Web 控制台](../README.md#web-控制台)里也能直接改。

> 本文是 [README](../README.md) 的补充。

## 配置说明

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

这些路径和总开关都可以改（相对配置文件所在目录）：

```yaml
enable: true            # 总开关：false = 整个程序什么都不做（调试时很省事）

storage:
  data_dir: data               # 数据目录
  credentials_file: credentials.yaml
  log_dir: logs
  log_file: miyouqian.log
```

### 账号

Web 控制台添加账号后，会在配置中保存账号名。扫码登录成功后，登录凭证会单独保存到 `data/credentials.yaml`。

通常不需要手动填写 cookie 或 stoken。

### 每账号独立任务配置

默认情况下所有账号共用一套任务配置。如果不同账号玩的游戏不一样，可以给单个账号设置独立任务：

1. 在账号卡片右侧点击「任务配置」按钮（滑块图标）
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
- 想直接写死也可以：`device.name` / `device.model` / `device.id` / `device.fp`（留空则由程序自动生成）

换指纹不影响已经登录的凭证；保存之后**下一次执行**就会用新指纹，不需要重启。

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

### 云游戏签到设置

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

### 米游币任务设置

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
  post_limit: 5        # 每次运行最多完成几个社区任务
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
| `jitter_minutes` | 随机延后分钟数（0~720，超出会被夹到区间内；默认 45） |
| `run_on_start` | 启动 Web 服务后是否立即执行一次 |

> `time` 记得**加引号**写成 `"09:00"`。不加引号时 YAML 会把它按"六十进制"解析成数字 `540`，程序会自动还原回 `09:00`，但写清楚更稳妥。

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

- 只有配置里的 `web.host` **和实际监听地址**都是本机地址时才免密。如果 `web.host` 写过 `0.0.0.0`，即使用 `--host 127.0.0.1` 启动，仍然要求密码
- `host` 为 `0.0.0.0` 或其他非本机地址时，必须设置密码才能使用
- 首次访问会显示密码设置页面，输入后自动保存（存储为哈希值）
- 也可以在配置文件中直接填写明文密码，启动时会自动转换为哈希
- 服务重启后需要重新输入密码

| 设置 | 说明 |
| --- | --- |
| `host` | 监听地址，`127.0.0.1` 仅本机，`0.0.0.0` 允许外部 |
| `port` | 监听端口，默认 `5890` |
| `password` | 访问密码，留空首次通过页面设置，也可直接填写明文 |

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
| `endpoints` | 自定义**国内直连**那条链路的查询接口（境外链路始终用内置接口），只要响应里包含 IP 即可，最多 5 个 |

判定依据：**同时探测两条链路**，任意一条出口在境外都会暂停。

| 链路 | 探测接口 | 说明 |
| --- | --- | --- |
| 国内直连 | ipip.net / 3322 / 淘宝 | 代理规则里多是中国大陆直连 |
| 境外流量 | ipify / ipinfo / myip | 米游社是境外服务，走的是这条路 |

为什么要分开查：**分流模式（规则模式）的代理下，国内直连、国外走代理，两边看到的出口 IP 不一样**。如果只查国内接口，会看到江苏的 IP 以为一切正常，但米游社实际是从香港出去的，一样会触发风控。分开探测后这种「假正常」能被识别出来。

**中国香港、中国澳门、中国台湾的网络出口与海外一样不算中国大陆**，同样会暂停。

Web 控制台的「IP 防护」面板可以开关、调整间隔、点「立即检测出口 IP」当场看两条链路的结果。

> 注意：判定依赖第三方查询接口。接口全部不可用且 `on_error: allow` 时会放行，不会因此卡住签到。

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
