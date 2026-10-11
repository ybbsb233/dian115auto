"""Linux 安装与 systemd 用户服务助手；安装步骤不会执行签到。"""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
UNIT = "dian115auto.service"


def unit_quote(value, exec_arg=False):
    """systemd 的路径/参数转义，不经过 shell。"""
    value = value.as_posix() if isinstance(value, Path) else str(value)
    if any(char in value for char in ("\n", "\r", "\x00")):
        raise ValueError("服务路径不能含换行或 NUL")
    value = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    if exec_arg:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def render_unit(root, config, environment_file, desktop=None, virtual_display=None):
    root, config = Path(root), Path(config)
    args = [root / ".venv/bin/python", root / "checkin.py", "serve", "--config", config]
    if virtual_display:
        args = [virtual_display, "-a", "--server-args=-screen 0 1280x800x24 -nolisten tcp", *args, "--headed"]
    elif desktop:
        args.append("--headed")
    command = " ".join(unit_quote(arg, exec_arg=True) for arg in args)
    desktop_lines = "".join("Environment=" + unit_quote(f"{key}={value}") + "\n"
                            for key, value in (desktop or {}).items())
    return (
        "[Unit]\nDescription=Dian115 daily check-in\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={unit_quote(root)}\nExecStart={command}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        f"EnvironmentFile=-{unit_quote(environment_file)}\n"
        + desktop_lines +
        "Restart=on-failure\nRestartSec=30\nTimeoutStopSec=240\nUMask=0077\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def ensure_xvfb():
    if shutil.which("xvfb-run") and shutil.which("xauth"):
        return
    if not shutil.which("apt-get"):
        raise ValueError("请用系统包管理器安装 Xvfb 和 xauth 后重试")
    run("sudo", "apt-get", "install", "-y", "xvfb", "xauth")


def display_mode(choice):
    if choice == "auto":
        return "desktop" if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") else "xvfb"
    return choice


def xvfb_command():
    executable = shutil.which("xvfb-run")
    if not executable or not shutil.which("xauth"):
        raise ValueError("未安装虚拟显示器，请先执行 setup")
    return [executable, "-a", "--server-args=-screen 0 1280x800x24 -nolisten tcp"]


def validate(config):
    if not PYTHON.is_file():
        raise ValueError("先执行 python3 deploy/linux.py setup")
    # 用项目虚拟环境校验配置（包括时区依赖），不访问网站。
    run(PYTHON, "-c", "from settings import load_settings; import sys; load_settings(sys.argv[1])", config)
    with config.open("rb") as stream:
        return tomllib.load(stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Linux 安装、登录与后台服务管理")
    parser.add_argument("command", choices=("setup", "login", "install-service", "start", "stop", "status", "logs"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--print", action="store_true", dest="print_unit", help="仅预览 install-service 的服务文件")
    parser.add_argument("--display", choices=("auto", "xvfb", "desktop", "headless"), default="auto",
                        help="登录/安装服务的显示模式，auto 在纯 SSH 中使用 Xvfb")
    args = parser.parse_args(argv)
    config = args.config.resolve()
    try:
        if sys.platform != "linux":
            raise ValueError("此部署助手只在 Linux 运行")
        if os.geteuid() == 0:
            raise ValueError("请使用普通 Linux 用户运行，不要 sudo 启动此助手；安装系统依赖时会单独请求 sudo")
        if args.command == "setup":
            if sys.version_info < (3, 11):
                raise ValueError("需要 Python 3.11+；Ubuntu 24.04 或 Debian 12 的系统 Python 满足要求")
            if not PYTHON.exists():
                if (ROOT / ".venv").exists():
                    raise ValueError(".venv 不是 Linux 虚拟环境，请将旧目录改名后重新安装")
                run(sys.executable, "-m", "venv", ROOT / ".venv")
            run(PYTHON, "-m", "pip", "install", "-r", ROOT / "requirements.txt")
            run(PYTHON, "-m", "playwright", "install", "--with-deps", "chromium")
            ensure_xvfb()
            validate(config)
            print("安装完成，没有执行签到。填写 .env 后执行 python3 deploy/linux.py login。")
        elif args.command == "login":
            validate(config)
            display = display_mode(args.display)
            if display == "headless":
                raise ValueError("login 使用有界面浏览器，请选择 xvfb 或 desktop")
            prefix = []
            if display == "xvfb":
                prefix = xvfb_command()
                run(PYTHON, "-c", "from settings import load_settings; import sys,os; load_settings(sys.argv[1]); "
                    "sys.exit(0 if os.getenv('DIAN115_EMAIL') and os.getenv('DIAN115_PASSWORD') else "
                    "'Xvfb 自动登录需要在 .env 中填写 DIAN115_EMAIL 和 DIAN115_PASSWORD')", config)
            elif not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise ValueError("desktop 模式需要可用的桌面显示环境")
            # 不自动关闭已有服务；登录命令的服务锁会提示先停止。
            run(*prefix, PYTHON, ROOT / "checkin.py", "login", "--config", config)
        elif args.command == "install-service":
            raw = validate(config)
            profile = (config.parent / raw.get("browser", {}).get("profile", ".state/browser")).resolve()
            data = (config.parent / raw.get("storage", {}).get("directory", "data")).resolve()
            identity = json.loads((data / "account.json").read_text(encoding="utf-8"))
            if not isinstance(identity, dict) or not identity.get("account_id") or identity.get("profile") != str(profile):
                raise ValueError("请先使用同一配置在服务器上完成 login")
            desktop = {}
            virtual_display = None
            display = display_mode(args.display)
            if display == "xvfb":
                virtual_display = xvfb_command()[0]
            elif display == "desktop":
                desktop = {key: os.environ[key] for key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY") if key in os.environ}
                if not (desktop.get("DISPLAY") or desktop.get("WAYLAND_DISPLAY")):
                    raise ValueError("headless=false 需要持续运行的图形桌面；请在该桌面内安装服务")
            config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            if display == "headless" and not raw.get("browser", {}).get("headless", True):
                raise ValueError("headless 模式需要 browser.headless=true")
            content = render_unit(ROOT, config, config_home / "dian115auto/api.env", desktop, virtual_display)
            if args.print_unit:
                print(content)
                return 0
            subprocess.run(["systemctl", "--user", "show-environment"], check=True,
                           cwd=ROOT, stdout=subprocess.DEVNULL)
            target = config_home / "systemd/user" / UNIT
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            run("systemctl", "--user", "daemon-reload")
            run("systemctl", "--user", "enable", UNIT)
            print(f"已安装并启用：{target}，尚未启动或签到。")
            print("先运行 sudo loginctl enable-linger \"$USER\"，再运行 python3 deploy/linux.py start。")
        elif args.command == "logs":
            run("journalctl", "--user", "-u", UNIT, "-n", "50", "-f")
        else:
            run("systemctl", "--user", args.command, UNIT)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"操作未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
