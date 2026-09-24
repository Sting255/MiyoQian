# 米游签部署指南（双账号 + 米游币 + 游戏签到）

本文档基于 [MiyoQian](https://github.com/nicklly/MiyoQian) 项目，配合 GitHub Actions 免费跑。

---

## 一、本地启动（用来扫码登录 + 测试）

### 1.1 安装 Python 3.11

去 https://www.python.org/downloads/release/python-3119/ 下载安装包，安装时**勾选 "Add Python to PATH"**。

### 1.2 安装 uv（Python 包管理器）

**Windows PowerShell：**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux / macOS：**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

装完重新打开终端，验证：
```bash
uv --version
```

### 1.3 准备项目

把 `MiyoQian` 整个文件夹解压到本地任意位置（比如 `D:\miyouqian`）。

进入项目目录（看到 `main.py`、`pyproject.toml` 的那个目录）。

**Windows：**
```powershell
cd D:\miyouqian
copy config.example.yaml config.yaml
```

**Linux / macOS：**
```bash
cd /path/to/MiyoQian
cp config.example.yaml config.yaml
```

### 1.4 安装依赖

```bash
uv venv --python 3.11
uv sync
```

> 装得慢的话用清华源：
> `uv sync --index-url https://pypi.tuna.tsinghua.edu.cn/simple`

### 1.5 启动 Web 控制台

```bash
uv run python main.py
```

启动后看到类似输出：
```
Web 控制台已启动: http://127.0.0.1:5890
```

浏览器打开 **http://127.0.0.1:5890**

### 1.6 添加两个账号

1. 顶部的"账号"区域，点 **"添加"** 按钮
2. 账号名改成 `main`（账号1）和 `account2`（账号2）方便识别
3. 点账号卡片的 **"登录"**，弹出二维码
4. 打开**米游社 APP** → **我的** → **左上角扫一扫** 扫码
5. 扫码成功后，账号卡片显示 UID = 登录成功

### 1.7 勾选任务

在"任务配置"区域：
- ✅ **游戏社区签到**（默认原神/星铁/绝区零开启）
- ✅ **米游币任务**（看帖、点赞、分享）
- 云游戏签到保持关闭（需要 token，后面再配）

### 1.8 手动测试

点右上角 **"立即执行"**，右侧日志区域会实时显示执行过程。

正常日志长这样：
```
✅ 账号 main - 原神签到成功：精锻用良矿 × 3
✅ 账号 account2 - 星穹铁道签到成功：星琼 × 50
✅ 账号 main - 米游币任务完成：看帖 3 + 点赞 5 + 分享 1
✅ 账号 account2 - 米游币任务完成
```

提示 "今日已签到" 是正常的，说明账号和配置都正常。

### 1.9 关闭本地控制台

测试 OK 后按 `Ctrl + C` 关闭 Web 控制台（这一步只是测试，真正跑是 GitHub Actions）。

---

## 二、部署到 GitHub Actions（白嫖免费跑）

### ⚠️ 重要：必须用 Use this template，不要 Fork！

> Fork 仓库的 Actions 运行时长会算到**上游**（原作者）仓库，会导致原作者的仓库因超时长被 GitHub 封禁。
> **请用 Use this template 创建到你自己账号下。**

### 2.1 创建仓库

1. 打开 https://github.com/nicklly/MiyoQian
2. 点右上角绿色 **"Use this template"** → **"Create a new repository"**
3. 填仓库名（比如 `my-miyouqian`），选 **Public** 或 **Private** 都行
4. 点 **"Create repository"**

### 2.2 上传配置和凭证

回到你本地项目目录，把生成的 `config.yaml` 和 `data/credentials.yaml` 复制一份备用（等下要复制内容）。

### 2.3 添加 GitHub Secrets

进入你刚才创建的仓库页面：

1. 点 **Settings** → **Secrets and variables** → **Actions**
2. 点 **"New repository secret"**，添加两个：

| 名称 | 内容 |
|------|------|
| `MIYOUQIAN_CONFIG` | 完整复制 `config.yaml` 的所有内容（注意保留 YAML 缩进和换行） |
| `MIYOUQIAN_CREDENTIALS` | 完整复制 `data/credentials.yaml` 的所有内容 |

### 2.4 启用 Actions

进入仓库的 **Actions** 页面：

- 如果提示 "Workflows aren't being run on this repository"，点 **"I understand my workflows, enable them"**
- 启用后**无论 Secrets 配没配好**，每天都会跑一次（如果 Secret 没配会报错，但不会出问题）

### 2.5 第一次手动跑测试

1. 进入 **Actions** → 选 **"米游签定时签到"**
2. 右边 **"Run workflow"** → **"Run workflow"**
3. 等 1-2 分钟，看日志底部有没有成功提示

### 2.6 修改执行时间

默认是**每天北京时间 09:20** 跑一次。改时间编辑 `.github/workflows/checkin.yml`：

```yaml
schedule:
  - cron: "20 9 * * *"      # 把这里改成你想要的时间
    timezone: "Asia/Shanghai"
```

cron 5 个字段分别是 `分钟 小时 日期 月份 星期`：
- `0 8 * * *` = 每天 8:00
- `30 9 * * *` = 每天 9:30
- `0 18 * * *` = 每天 18:00

> ⚠️ **避开整点**（`0 9 * * *` 这种），整点附近 Actions 排队会延迟。

### 2.7 配置推送通知（强烈建议）

不配推送的话，跑失败了你收不到通知，会漏签。

最快的方式是 **pushplus**（微信推送）：
1. 用 GitHub 登录 https://www.pushplus.plus/
2. 复制你的 token
3. 在 GitHub 仓库里编辑 `config.yaml` 推送段：
   ```yaml
   push:
     error_only: true            # 只在失败时推送，不刷屏
     channels:
       - provider: pushplus
         enable: true
         token: "你的 pushplus token"
   ```
4. 重新把 `config.yaml` 内容复制更新到 GitHub Secret 的 `MIYOUQIAN_CONFIG` 里

---

## 三、日常维护

### 凭证过期了怎么办？

如果突然报"登录失效"：
1. 在本地重新跑 `uv run python main.py`
2. 扫码重新登录所有账号（会更新 `data/credentials.yaml`）
3. 把更新后的 `data/credentials.yaml` 内容重新复制到 GitHub Secret 的 `MIYOUQIAN_CREDENTIALS`

### 如何只跑某个账号？

GitHub Actions 页面 → Run workflow → **account** 输入框填账号名（比如 `main`），留空 = 全部跑。

### 如何只跑游戏签到（跳过米游币）或反过来？

Run workflow → **执行模式** 下拉选：
- 全部任务
- 只执行游戏社区/云游戏签到
- 只执行米游币社区任务

### 遇到验证码怎么办？

米游币任务偶尔会弹验证码。两种解法：
1. **关掉米游币任务**（`config.yaml` 里 `features.bbs_tasks: false`），只保留游戏签到
2. **配打码狗自动识别**：去 https://www.damagou.com/ 注册充值，在网页「验证码识别」里填 userkey 并启用（`captcha.channels` 里 `local` 固定在 `[0]`、`damagou` 在 `[1]`）

---

## 四、遇到问题怎么办？

- 看 GitHub Actions 页面里的执行日志
- 看 `logs/miyouqian.log`
- 大部分问题（登录失效、Cookie 过期）重新跑一次扫码就行
- 米哈游规则变化时项目可能临时失效，等作者修复
