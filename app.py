"""命令入口：login 保存身份，run 仅输出结果，serve 提供 API、调度与记录。"""
import argparse
from dataclasses import replace
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import signal
import threading
from zoneinfo import ZoneInfoNotFoundError

from filelock import FileLock, Timeout
from api import make_server
from checkin import Browser
from runtime import Engine, Scheduler, execute_safely
from settings import load_settings
from storage import atomic_json, read_json

ROOT = Path(__file__).resolve().parent


def output(result):
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


def setup_logging(cfg):
    """仅由常驻服务调用；日志时间使用配置时区，并按大小轮转。"""
    folder = cfg.data / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dian115.service")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    class Formatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            return datetime.fromtimestamp(record.created, cfg.zone).isoformat(timespec="seconds")

    formatter = Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.StreamHandler(), RotatingFileHandler(folder / "service.log",
                     maxBytes=1_000_000, backupCount=5, encoding="utf-8")):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def serve(cfg):
    """同一数据目录只允许一个服务；停止时等待正在进行的操作和写入结束。"""
    cfg.data.mkdir(parents=True, exist_ok=True)
    with FileLock(str(cfg.data / "service.lock"), timeout=0):
        # 将本地记录绑定到 login 确认的账号和浏览器目录，防止账号间混用状态。
        identity = read_json(cfg.data / "account.json", {})
        if (not isinstance(identity, dict) or not identity.get("account_id")
                or identity.get("profile") != str(cfg.profile)):
            raise ValueError("请先使用同一配置执行 login，确认本地账号身份。")
        logger = setup_logging(cfg)
        engine = Engine(cfg, identity["account_id"])
        scheduler = Scheduler(engine)
        server = make_server(engine)
        stop = threading.Event()
        worker = threading.Thread(target=scheduler.run, args=(stop,), name="daily-scheduler")
        server.scheduler_thread = worker
        http_worker = threading.Thread(target=server.serve_forever, name="http-server")
        old_sigterm = signal.signal(signal.SIGTERM, lambda *_: stop.set())
        worker.start()
        http_worker.start()
        logger.info("服务启动 %s:%s，签到模式=%s，定时=%s %s，时区=%s", cfg.host, cfg.port,
                    cfg.mode, cfg.enabled, cfg.at, cfg.zone)
        try:
            while not stop.wait(1):
                if not worker.is_alive() or not http_worker.is_alive():
                    raise RuntimeError("服务工作线程停止")
        except KeyboardInterrupt:
            pass
        finally:
            # 先停止接收新请求，再等待调度及 HTTP 请求线程完成，最后释放服务锁。
            stop.set()
            server.shutdown()
            http_worker.join()
            worker.join()
            server.server_close()
            signal.signal(signal.SIGTERM, old_sigterm)
            logger.info("服务已停止")
        if scheduler.fatal:
            raise RuntimeError("调度异常退出")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dian115 签到：单次运行或常驻 API/调度服务（Python 3.11+）。")
    parser.add_argument("command", choices=("login", "run", "serve"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--mode", choices=("normal", "lucky"), help="覆盖配置中的签到模式")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--browser", choices=("chromium", "chrome", "msedge"))
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--login-timeout", type=int)
    args = parser.parse_args(argv)
    try:
        cfg = load_settings(args.config)
        updates = {}
        for arg, field in (("mode", "mode"), ("browser", "channel"), ("timeout", "timeout"),
                           ("login_timeout", "login_timeout")):
            value = getattr(args, arg)
            if value is not None:
                if field in ("timeout", "login_timeout") and not 1 <= value <= 3600:
                    raise ValueError("超时时间必须为 1..3600 秒")
                updates[field] = value
        if args.headed:
            updates["headless"] = False
        if args.profile:
            updates["profile"] = args.profile.resolve()
        cfg = replace(cfg, **updates)
        if args.command == "serve":
            serve(cfg)
            return 0
        browser = Browser(cfg)
        if args.command == "login":
            cfg.data.mkdir(parents=True, exist_ok=True)
            with FileLock(str(cfg.data / "service.lock"), timeout=0):
                result = execute_safely(browser, "login")
                if "account_id" in result:
                    atomic_json(cfg.data / "account.json", {
                        "account_id": result["account_id"], "profile": str(cfg.profile),
                    })
                    result["status"] = "ok"
        else:
            # 单次执行直接调用浏览器，绕过服务层的本地状态、日志及签到记录。
            result = execute_safely(browser, "checkin", mode=cfg.mode)
            result["observed_at"] = datetime.now(cfg.zone).isoformat()
        output(result)
        return 0 if result.get("status") in ("ok", "success", "already_signed") else (2 if result.get("status") == "login_required" else 1)
    except Timeout:
        output({"status": "busy", "message": "服务已在运行；重新登录前请先停止服务。"})
        return 1
    except (OSError, ValueError, ZoneInfoNotFoundError) as exc:
        output({"status": "failed", "message": "配置或本地文件错误：" + str(exc)})
        return 1
    except KeyboardInterrupt:
        return 130
    except RuntimeError:
        output({"status": "failed", "message": "服务工作线程异常停止，请检查日志并重启。"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
