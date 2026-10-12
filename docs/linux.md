# Linux 部署

下载项目 → 安装依赖 → 填写 `.env` → 自动登录 → 启动后台服务。纯 SSH 默认使用 Xvfb 虚拟显示器，不必先安装完整桌面。程序负责每天签到，无需额外配置 cron。

建议 Ubuntu 24.04 或 Debian 12，Python 3.11+，使用同一普通 Linux 用户完成所有步骤。需要 sudo 安装系统依赖的权限，以及可下载 Python 包和 Chromium 的网络。

## 1. 安装

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
git clone https://github.com/ybbsb233/dian115auto.git
cd dian115auto
python3 deploy/linux.py setup
```

助手创建 `.venv`，安装 Python 依赖、Chromium、Xvfb 和 xauth；可能要求 sudo 密码。安装不登录、不签到、不启动服务，也不覆盖已有配置或登录资料。不要用 root 运行助手。

## 2. 配置和登录

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

填写网站账号密码：

```dotenv
DIAN115_EMAIL=你的登录邮箱
DIAN115_PASSWORD='你的密码'
```

密码包含空格或 `#` 时用引号包住。真实 `.env` 已被 Git 忽略，系统环境变量优先。自定义 `--config` 时，`.env` 应放在对应配置文件的目录。

修改 `config.toml` 中的签到模式和时间，然后执行：

```bash
python3 deploy/linux.py login --display xvfb
```

浏览器以有界面模式在虚拟显示器里运行，自动填写并提交账号密码，确认账号后关闭并保存凭证；不会签到。这个步骤不需要可见桌面。

`--display` 默认是 `auto`：纯 SSH 自动选 Xvfb，有桌面时选桌面。服务器部署建议登录和安装服务都显式使用 `--display xvfb`，避免两次操作选择不同显示方式。

**如果网站要求人工验证码，Xvfb 本身不能让你远程操作页面。** 此时需要远程桌面/VNC 或配置好的 X11 转发，然后执行 `login --display desktop` 完成人工验证。目前 Windows 自动登录未遇到人工验证码，Linux 网络和浏览器环境可能不同，不能保证同样通过。

登录资料保存在 `.state/browser/`，账号身份保存在 `data/account.json`。请在服务器重新登录，不要复制 Windows 的 `.venv` 或假定 Windows 浏览器资料能跨系统使用。

## 3. 后台运行

```bash
python3 deploy/linux.py install-service --display xvfb
sudo loginctl enable-linger "$USER"
python3 deploy/linux.py start
python3 deploy/linux.py status
```

助手自动生成当前用户的 systemd 服务，填入项目与配置的实际路径，后台服务也用 Xvfb 运行有界面浏览器。**无需修改 `browser.headless`**，服务命令会以 `--headed` 覆盖它。Xvfb 由服务自动启动和关闭，不需要单独保持 SSH 或远程桌面连接。

服务写入 `~/.config/systemd/user/dian115auto.service`（遵循 `XDG_CONFIG_HOME`）。`install-service` 只安装并启用，不立即启动；`start` 才开始调度。默认北京时间 09:00 普通签到，错过当天时间时可能在启动后立即补签。

`enable-linger` 让用户服务在退出 SSH 后继续运行并随服务器启动。服务异常退出后自动重启；服务器仍需开机联网。容器或精简系统可能没有 systemd 用户服务，遇到用户服务管理器连接错误请让管理员检查。

## 4. 日常管理

```bash
python3 deploy/linux.py logs   # 查看日志，Ctrl+C 退出查看
python3 deploy/linux.py stop
python3 deploy/linux.py start
```

程序日志也在 `data/logs/service.log`。修改配置后需停止并重新启动。凭证过期后先停止服务，再执行 `login --display xvfb`，最后启动；程序不会在签到失败时反复提交账号密码。

自定义配置时，在 `setup`、`login`、`install-service` 后统一添加 `--config /绝对路径/config.toml`。改变项目路径、配置路径或显示模式后，重新安装服务。

服务默认只监听服务器的 `127.0.0.1:8080`。在本地电脑使用 SSH 转发：

```bash
ssh -L 18080:127.0.0.1:8080 用户名@服务器
# 本地访问 http://127.0.0.1:18080/health
```

API 密钥可在 `.env` 中设置 `DIAN115_API_KEY`；也兼容 `~/.config/dian115auto/api.env`（权限设为 `600`，遵循 `XDG_CONFIG_HOME`）。修改后重启服务。

更新前先停止服务，更新代码后执行 `setup` 安装新版依赖和浏览器，再启动。要卸载开机自启，可执行 `systemctl --user disable --now dian115auto.service`；登录资料保留在项目目录。

## 验证范围

已在 Debian 13 x86-64 验证自动登录、Chromium + Xvfb、systemd 用户服务、定时运气签到和 HTTP 查询。其他服务器环境可能遇到网站验证；长期登录有效期和跨日运行仍需观察。

依赖安装参考 [Playwright 浏览器文档](https://playwright.dev/python/docs/browsers)；Linux 有界面运行使用 Xvfb 的方式参考 [Playwright CI 文档](https://playwright.dev/python/docs/ci)。
