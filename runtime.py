"""常驻服务的业务与调度层：确认签到结果、保存记录，按日控制执行及重试。"""
from datetime import datetime, timedelta
import logging
import threading

from playwright.sync_api import Error as PlaywrightError
from checkin import CheckinError, Browser
from storage import Store, read_json, atomic_json

LOG = logging.getLogger("dian115.service")


def execute_safely(browser, operation, **kwargs):
    """统一浏览器异常；网络中断可能发生在提交之后，因此返回未知而非未签到。"""
    try:
        return browser.execute(operation, **kwargs)
    except CheckinError as exc:
        return {"status": exc.status, "message": str(exc)}
    except PlaywrightError as exc:
        # 启动失败发生在访问网站之前，可明确报错；不输出可能含路径或凭据的原始异常。
        detail = str(exc)
        if "BrowserType.launch" in detail:
            if "spawn UNKNOWN" in detail:
                message = "浏览器启动失败（spawn UNKNOWN），尚未访问网站。Windows 可尝试 --browser msedge，并在配置中使用同一浏览器。"
            elif "Executable doesn't exist" in detail:
                message = "未找到浏览器。使用 Chromium 时请先运行 python -m playwright install chromium；其他浏览器需先在系统中安装。"
            else:
                message = "浏览器未能启动，尚未访问网站。请检查浏览器安装、资料目录是否被占用，以及图形桌面是否可用。"
            return {"status": "failed", "message": message}
        hint = "登录命令已经显示浏览器窗口，请检查页面加载或窗口是否被关闭。" if operation == "login" else "可用 --headed 查看。"
        return {"status": "unknown", "message": "浏览器或网络异常，操作结果未确认；" + hint}


class Engine:
    """协调 API 和定时任务；单次 CLI 执行不经过此类，避免写入记录和日志。"""
    def __init__(self, settings, account_id, browser=None, clock=None):
        self.cfg = settings
        self.account_id = account_id
        self.browser = browser or Browser(settings)
        self.clock = clock or (lambda: datetime.now(settings.zone))
        self.store = Store(settings.data, account_id)
        # 线程锁覆盖网站操作及记录保存；浏览器层另有跨进程锁保护资料目录。
        self.lock = threading.Lock()

    def status(self):
        """只读当前账号、配置时区下的当天记录，不访问网站。"""
        return self.store.status(self.clock().date().isoformat())

    def account(self):
        if not self.lock.acquire(blocking=False):
            return {"status": "busy", "message": "有操作正在进行。"}
        try:
            return execute_safely(self.browser, "account", expected_account=self.account_id)
        finally:
            self.lock.release()

    def checkin(self, mode=None, scheduled=False):
        """定时执行可按本地记录跳过；手动 API 调用始终尝试网站签到。"""
        mode = mode or self.cfg.mode
        if mode not in ("normal", "lucky"):
            raise ValueError("mode 必须为 normal 或 lucky")
        if not self.lock.acquire(blocking=False):
            return {"status": "busy", "message": "有操作正在进行。"}
        try:
            started = self.clock()
            day = started.date().isoformat()
            if scheduled and self.store.get(day):
                LOG.info("%s 本地已确认签到，跳过定时执行", day)
                return {"status": "skipped", "date": day, "account_id": self.account_id}
            result = execute_safely(self.browser, "checkin", mode=mode, expected_account=self.account_id)
            finished = self.clock()
            result.update({"date": day, "observed_at": finished.isoformat(),
                           "account_id": self.account_id, "requested_mode": mode,
                           "source": "scheduled" if scheduled else "api"})
            if result["status"] in ("success", "already_signed"):
                if started.date() != finished.date():
                    # 尚未验证网站响应中的日期字段，跨午夜时不猜测归属日期。
                    result.update(date=None, record_saved=False,
                                  message="请求跨越日期边界，网站已确认结果，但未归档到某一天。")
                else:
                    try:
                        self.store.save(result)
                        result["record_saved"] = True
                    except (OSError, ValueError):
                        # 网站结果和本地落盘是两件事：保存失败不能推翻已确认的签到。
                        result.update(record_saved=False, message="网站已确认签到，但本地记录保存失败，请检查数据目录。")
                LOG.info("%s %s签到 %s 积分变化=%s 余额=%s 已保存=%s", day,
                         "普通" if mode == "normal" else "运气", result["status"],
                         result.get("points_change"), result.get("balance"), result["record_saved"])
            else:
                LOG.warning("%s 签到 %s：%s", day, result["status"], result.get("message", ""))
            return result
        finally:
            self.lock.release()


class Scheduler:
    """按配置时区检查每日任务，并将尝试次数持久化以限制重启后的重试。"""
    def __init__(self, engine):
        self.engine = engine
        self.cfg = engine.cfg
        self.path = engine.store.directory / "schedule.json"
        self.started = engine.clock()
        self.fatal = False
        # 关闭补执行时，启动时已错过的当天不再执行；次日恢复正常调度。
        self.skip_start_day = (not self.cfg.catch_up and self.started.time().replace(tzinfo=None) > self.cfg.at)

    def tick(self):
        """检查一次是否到期；只有结果未知或浏览器忙才进入有限重试。"""
        now = self.engine.clock()
        if not self.cfg.enabled or now.time().replace(tzinfo=None) < self.cfg.at:
            return
        if self.skip_start_day and now.date() == self.started.date():
            return
        day = now.date().isoformat()
        state = read_json(self.path, {})
        if not isinstance(state, dict):
            raise ValueError("调度记录格式错误")
        if state.get("date") != day:
            state = {"date": day, "attempts": 0, "done": False}
        if state.get("done"):
            return
        if state.get("retry_at") and now < datetime.fromisoformat(state["retry_at"]):
            return
        if self.engine.store.get(day):
            state["done"] = True
            atomic_json(self.path, state)
            LOG.info("%s 本地已确认签到，跳过定时执行", day)
            return
        if state["attempts"] >= self.cfg.retries + 1:
            state["done"] = True
            atomic_json(self.path, state)
            return
        # 请求前先扣减当天尝试预算，避免进程在请求后崩溃导致重启后无限尝试。
        state["attempts"] += 1
        state["retry_at"] = (now + timedelta(seconds=self.cfg.retry_seconds)).isoformat()
        atomic_json(self.path, state)
        result = self.engine.checkin(scheduled=True)
        state["last_status"] = result["status"]
        # done 表示当天调度已结束，不等于签到成功；成功状态以签到记录为准。
        state["done"] = (result["status"] not in ("unknown", "busy") or
                         state["attempts"] >= self.cfg.retries + 1)
        state["retry_at"] = (self.engine.clock() + timedelta(seconds=self.cfg.retry_seconds)).isoformat()
        atomic_json(self.path, state)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except (OSError, ValueError, TypeError, KeyError):
                LOG.error("调度或本地存储异常，请检查配置与数据目录；60 秒后再次检查。")
                stop.wait(60)
                continue
            except Exception:
                LOG.error("调度发生未预期异常，已停止调度，请重启服务并检查。")
                self.fatal = True
                stop.set()
                return
            # 可中断等待：退出信号到来时无需等完整的轮询间隔。
            stop.wait(1)
