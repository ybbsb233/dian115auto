# Dian115 自动签到

为 [Dian115 资源网站](https://m.dian115.com)编写的 Python 自动签到工具。首次在浏览器中登录（支持自动填写账号密码），之后每天按设定时间签到，输出积分结果并保存记录。

支持普通签到和运气签到、失败后的有限重试，以及可选的 HTTP API。适用于 Python 3.11+、Windows 和 Linux。这是第三方工具，需要自己的 Dian115 网站账号。

## 快速开始（Windows）

下载或克隆本项目，在项目目录打开 PowerShell：

```powershell
git clone https://github.com/ybbsb233/dian115auto.git
cd dian115auto
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

**1. 登录账号**

复制 `.env.example` 为 `.env`，填写网站登录邮箱和密码：

```dotenv
DIAN115_EMAIL=你的登录邮箱
DIAN115_PASSWORD=你的密码
```

`.env` 位于配置文件所在目录，已被 Git 忽略；系统环境变量优先。密码含 `#` 或空格时请用引号包住值。

```powershell
.\.venv\Scripts\python.exe checkin.py login
```

程序会在弹出的浏览器中自动填写并提交账号密码。如果网站要求人机验证，仍需人工完成；两项凭证都未配置时使用手动登录。程序确认账号后自动关闭浏览器并保存登录资料，`login` 不签到。

**2. 试运行一次**

```powershell
.\.venv\Scripts\python.exe checkin.py run
```

这会实际尝试普通签到，控制台显示结果。单次运行不保存签到记录或日志；后续服务通过网站的“已签到”响应确认当天状态。

**3. 开始每天自动签到**

```powershell
.\.venv\Scripts\python.exe checkin.py serve
```

默认每天北京时间 **09:00 普通签到**，错过当天时间会在启动时补执行。保持程序运行、电脑开机且联网，按 `Ctrl+C` 停止。关闭终端或电脑后不会继续签到；Windows 开机自启需要自行配置，Linux 可使用随附的 systemd 示例。

## 改时间或签到方式

修改 [config.toml](config.toml)，然后重启服务：

```toml
[schedule]
time = "09:00"
timezone = "Asia/Shanghai"

[checkin]
mode = "normal" # normal 普通签到；lucky 运气签到
```

以上是配置片段，请修改文件中已有的对应项目。运气签到可能获得 **0 分或扣分**，可用下面的命令尝试一次：

```powershell
.\.venv\Scripts\python.exe checkin.py run --mode lucky
```

| 命令 | 用途 |
| --- | --- |
| `login` | 自动或手动登录，保存账号与浏览器资料 |
| `run` | 立即尝试一次签到，结果输出到终端 |
| `serve` | 持续运行每日调度、记录日志，并提供本机 API |

个人设置可保存在 `config.local.toml`，该文件不会上传到 GitHub。每次使用它时，三个命令都要加同一参数，例如：

```powershell
.\.venv\Scripts\python.exe checkin.py serve --config config.local.toml
```

## 常见问题

- **提示登录失效**：先停止服务，再执行 `login`，完成后重新启动。登录和服务需使用同一配置、浏览器及资料目录。
- **浏览器无法启动或验证失败**：可在配置中将 `browser.channel` 改为 `msedge`（需要已安装 Edge），或将 `browser.headless` 改为 `false` 显示窗口完成验证。更换浏览器后重新登录。
- **找不到 Python 或版本过低**：安装 Python 3.11 或更高版本，再重新创建虚拟环境。从另一台电脑复制来的 `.venv` 通常需要在当前电脑重建。
- **想看历史或日志**：常驻服务的签到记录位于 `data/checkins/`，日志位于 `data/logs/service.log`；本地无记录只表示状态未知。

## Linux

提供安装和后台服务助手，完整步骤见 [Linux 部署指南](docs/linux.md)。建议 Ubuntu 24.04 或 Debian 12，使用普通 Linux 用户：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
git clone https://github.com/ybbsb233/dian115auto.git
cd dian115auto
python3 deploy/linux.py setup
cp .env.example .env
chmod 600 .env
nano .env                              # 填写网站邮箱和密码
python3 deploy/linux.py login --display xvfb
python3 deploy/linux.py install-service --display xvfb # 只安装，不启动
sudo loginctl enable-linger "$USER"
python3 deploy/linux.py start           # 启动每日签到，错过时间可能立即补签
```

助手会安装 Xvfb 虚拟显示器，登录和服务都能在纯 SSH 中以有界面模式自动运行浏览器，无需完整桌面。网站要求人工验证时仍需远程桌面或 X11 转发。Linux 部署尚未在实际服务器验证。

## 更多说明

完整配置、调度和重试规则、HTTP API、访问密钥及数据格式见[使用与 API 参考](docs/usage.md)。API 默认仅监听本机 `127.0.0.1:8080`，日常签到无需调用 API。

`.state/` 保存浏览器登录凭据，`data/` 保存账号信息与运行记录，均已被 Git 忽略。**不要上传或分享这些目录**；使用自定义目录时也应加入 `.gitignore`。

网站变化、人机验证和登录过期可能影响运行。2026-10-12 已在 Windows 实测运气签到，确认奖励、余额、倍率及当天日历记录；普通签到、登录有效期和每日调度仍需实测。

## 项目结构与测试

```text
checkin.py          网站浏览器操作与命令入口
app.py              登录、单次运行、常驻服务
settings.py         配置读取和校验
runtime.py          每日调度与签到记录管理
storage.py          本地 JSON 存储
api.py              HTTP API
config.toml         通用默认配置
deploy/             Linux 服务示例
tests/              自动化测试
docs/usage.md       详细使用说明
```

安装依赖和 Chromium 后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试不会登录或操作真实账号。

若使用已安装的 Edge，可先在 PowerShell 设置 `$env:DIAN115_TEST_BROWSER = 'msedge'` 再运行测试，无需下载测试用 Chromium。该变量只影响测试。
