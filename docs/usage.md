# 使用与 API 参考

Python 3.11+，支持 Windows / Linux。通过网站正常浏览器流程工作，不依赖官方 API Key。

## 三种命令

| 命令 | 行为 |
|---|---|
| `login` | 打开浏览器，从 `.env` 自动填写账号密码（未配置时手动登录），保存登录资料及账号身份；不签到 |
| `run` | 不检查本地签到状态，直接尝试一次；结果输出到控制台，不写日志、不保存签到记录 |
| `serve` | 提供 HTTP API 和每日调度；记录日志及已确认的签到结果 |

`run` 仍会读取/更新浏览器登录资料并使用浏览器锁，这不属于签到记录。`serve` 需要先通过同一配置执行一次 `login` 以确认账号身份。

## Windows 快速开始

在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium

# 首次登录，完成必要的人机验证；浏览器自动关闭后表示流程结束
.\.venv\Scripts\python.exe checkin.py login

# 手动测试：正常/运气签到；不会写入签到记录或日志
.\.venv\Scripts\python.exe checkin.py run
.\.venv\Scripts\python.exe checkin.py run --mode lucky

# 常驻服务；Ctrl+C 停止
.\.venv\Scripts\python.exe checkin.py serve
```

默认配置是 `config.toml`，不依赖执行命令时的当前目录。可用 `--config C:\path\config.local.toml` 指定其他配置。配置内相对路径以配置文件所在目录为基准，修改后重启服务生效。

`--headed` 显示浏览器；`--browser msedge` 或 `--browser chrome` 使用系统浏览器。登录和服务应使用同一浏览器、同一资料目录；不要将资料目录指向自己的日常浏览器。重新登录前先停止服务。

## Linux

安装、自动登录和后台服务管理见 [Linux 部署指南](linux.md)。统一使用 deploy/linux.py，无需手动编写 systemd 服务文件。

## 配置与调度

```toml
[schedule]
enabled = true
time = "09:00"
timezone = "Asia/Shanghai"
catch_up = true
retries = 2
retry_seconds = 60

[checkin]
mode = "normal"

[browser]
channel = "chromium"
profile = ".state/browser"
headless = true
timeout = 45
login_timeout = 300

[storage]
directory = "data"

[api]
host = "127.0.0.1"
port = 8080
key_env = "DIAN115_API_KEY"
```

- `normal` 普通签到；`lucky` 运气签到，可能获得 0 分或扣分，两种情况均算成功。默认普通模式，可自行选择。
- 到点先读当前账号当天的本地确认记录；已签到则跳过；未知则尝试签到。
- `catch_up=true`：启动/恢复时若当天已经到点，会补执行。只补当天，不补过去日期。关闭时，晚于签到时间启动会等待次日。
- `retries=2` 指初次之外最多再试两次，只重试结果未知或浏览器忙；登录失效、明确拒绝不会重试。间隔从上一次操作结束计算。
- 调度次数保存在本地，重启不会重置当天的重试预算。登录失效修复后，可通过 `POST /checkin` 手动补签；次日调度自动恢复。
- 关闭 `schedule.enabled` 只关闭定时签到，API 仍可用。
- 签到日期按配置时区归档，默认北京时间。跨越午夜且无法确认归属日期的响应不会标记任何一天为已签到。
- 浏览器锁同时约束 CLI、API 和调度；服务请求并发时返回 `busy`，不并行操作账号。

## HTTP API

默认地址 `http://127.0.0.1:8080`。下面是**本项目自己的返回格式**，不是网站原始响应。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务与调度线程状态，不访问网站 |
| GET | `/account` | 访问网站查询账号标识、昵称、积分余额 |
| GET | `/checkin/status` | 仅读取本地当天记录，返回 `signed` 或 `unknown` |
| POST | `/checkin` | 直接尝试签到，不因本地记录跳过；默认配置模式，可传 `mode` |
| GET | `/checkin/history` | 本地确认历史；支持 `since`、`until`、`page`、`size` |

```powershell
Invoke-RestMethod http://127.0.0.1:8080/checkin/status
Invoke-RestMethod http://127.0.0.1:8080/account
Invoke-RestMethod http://127.0.0.1:8080/checkin -Method Post -ContentType 'application/json' -Body '{"mode":"lucky"}'
Invoke-RestMethod 'http://127.0.0.1:8080/checkin/history?since=2026-10-01&until=2026-10-31&page=1&size=20'
```

Linux：

```bash
curl http://127.0.0.1:8080/checkin/status
curl -X POST http://127.0.0.1:8080/checkin -H 'Content-Type: application/json' -d '{"mode":"lucky"}'
```

成功响应示例（示意，非真实账号结果）：

```json
{
  "status": "success",
  "mode": "lucky",
  "requested_mode": "lucky",
  "points_change": 0,
  "multiplier": 0,
  "balance": 175,
  "account_id": "123",
  "date": "2026-10-11",
  "observed_at": "2026-10-11T09:00:03+08:00",
  "source": "api",
  "record_saved": true
}
```

`multiplier` 为网站返回的倍率（可能为 0 或负数）；缺失或无效时为 `null`，不从奖励积分推算。新保存的成功记录保留该字段，状态和历史接口可查询；旧记录可能没有此字段。

`already_signed` 表示网站确认已签到；`mode`、积分变化、余额和倍率可能为 `null`，不能编造当天之前的奖励。`requested_mode` 仅表示本次尝试选择的模式。已有成功记录不会被之后的“已签到”或错误覆盖。

`login_required`：网站身份失效；`busy`：浏览器或服务正在处理请求；`unknown`：结果未确认；`failed`：网站明确拒绝等错误。网站确认成功但磁盘写入失败时仍返回网站结果，同时 `record_saved=false`，日志记录保存失败。

HTTP：成功/本地状态查询为 200；需要身份或 API 密钥为 401；忙为 409；网站操作未知/失败为 502；参数错误为 400；本地存储异常为 500。客户端应同时检查 JSON 的 `status`。`GET /health` 仅证明服务存活，不证明网站登录有效。

API 不提供网站积分流水接口，本版签到判断不依赖流水。历史接口只展示本服务保存的确认记录，异常尝试查看日志。

## API 访问密钥

可通过环境变量设置密钥，设置后所有接口都需要 Bearer 鉴权：

```powershell
$env:DIAN115_API_KEY = '请替换为至少24字符的随机密钥'
.\.venv\Scripts\python.exe checkin.py serve
# 请求时添加 -Headers @{Authorization="Bearer $env:DIAN115_API_KEY"}
```

对外监听（如 `0.0.0.0`）必须设置至少 24 字符密钥。HTTP 服务不自带 TLS；远程使用推荐 SSH 隧道或 HTTPS 反向代理。不配置密钥时仅允许本机访问，不接受浏览器跨域请求。

## 数据与日志

```text
.state/browser/               浏览器资料及 session-cookies.json（敏感）
data/account.json             最近 login 确认的账号及资料目录
data/checkins/<账号哈希>/      YYYY-MM-DD.json 确认记录、schedule.json 调度状态
data/logs/service.log          常驻服务日志，自动轮转
```

Cookie 等同于登录凭据，请勿上传或分享。项目 `.gitignore` 排除默认的浏览器资料、数据、日志和本地配置；自定义其他目录时自行排除。Linux 下敏感 JSON 通过临时文件以仅当前用户可读写的权限创建，再原子替换。

单次运行不保存签到记录，之后服务仍可能尝试一次，由网站的“今日已签到”响应补齐记录。浏览器与数据目录需要保留；换目录后本地无记录只能表示未知。日志仅由 `serve` 创建，`run` 和 `login` 不创建日志。

每日定时执行由 `serve` 内置调度负责，无需额外注册 Windows 计划任务。

## 验证范围与限制

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖浏览器按钮、请求响应、0/负分、重复签到、本地记录隔离、并发、调度重试、HTTP 鉴权、单次模式无记录/无日志及会话 Cookie 恢复。接口测试使用模拟网站响应，不会操作真实账号。

已验证 Windows 和 Debian 13 的运气签到、Linux 自动登录及定时运行，以及 HTTP 状态和历史查询。普通签到、长期登录有效期和跨午夜边界仍需进一步验证。网站要求人工验证时需在浏览器中处理。
