# Dian115 自动签到

为 [Dian115 资源网站](https://m.dian115.com)编写的 Python 自动签到工具。首次在浏览器中手动登录，之后每天按设定时间签到，输出积分结果并保存记录。

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

```powershell
.\.venv\Scripts\python.exe checkin.py login
```

在弹出的浏览器中登录并完成网站验证。程序确认账号后会自动关闭浏览器并保存登录资料。

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
| `login` | 手动登录，保存账号与浏览器资料 |
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

首次登录需要图形桌面或远程桌面；无桌面的 SSH 会话无法完成交互登录。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python checkin.py login
.venv/bin/python checkin.py serve
```

开机自启示例见 [deploy/dian115.service](deploy/dian115.service)，安装步骤见[使用与 API 参考](docs/usage.md#linux)。Linux 部署示例尚未在实际服务器验证。

## 更多说明

完整配置、调度和重试规则、HTTP API、访问密钥及数据格式见[使用与 API 参考](docs/usage.md)。API 默认仅监听本机 `127.0.0.1:8080`，日常签到无需调用 API。

`.state/` 保存浏览器登录凭据，`data/` 保存账号信息与运行记录，均已被 Git 忽略。**不要上传或分享这些目录**；使用自定义目录时也应加入 `.gitignore`。

网站变化、人机验证和登录过期可能影响运行。自动化测试使用模拟响应，不代表真实账号流程已验证；网站完整响应、登录有效期及北京时间日界线仍需实测。

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
