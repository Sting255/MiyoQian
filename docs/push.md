# 推送设置

跑完把结果推给你。支持 pushplus / QQ / Telegram / 钉钉 / 飞书 / 邮件。

> 本文是 [README](../README.md) 的补充。

## 推送设置

推送可以在 Web 控制台中配置。开启后，任务结束时会发送结果通知。

如果只想失败时通知，勾选“仅失败时推送”，或设置：

```yaml
push:
  error_only: true     # 只在失败时推送
  per_account: true    # 每跑完一个账号就推一次，不必等全部账号跑完
```

> 单个渠道自己的开关是 `push.channels[].enable`，至少有一个渠道 `enable: true` 才会推送。

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

### QQ（OneBot）

走 OneBot 实现的 QQ 机器人（例如 NapCat、Lagrange）：

```yaml
push:
  channels:
    - provider: qq
      enable: true
      push_url: "http://127.0.0.1:3000"   # OneBot 的 HTTP 接口地址
      access_token: ""                    # 机器人配置了 token 就填
      send_id: "123456789"                # 私聊填 QQ 号，群聊填群号
      msg_type: "private"                 # private = 私聊，group = 群聊
```

> QQ 通道只发「结果」：进度行、汇总行、成功项会被过滤掉，并且正文超长会截断。
