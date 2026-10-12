"""网站浏览器适配层：通过原有页面操作并捕获响应，不自行绕过登录验证。"""
import math
import os
import time
from urllib.parse import urlsplit
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SITE = "https://m.dian115.com"
SIGNIN_PATH = "/api/portal/signin"

class CheckinError(Exception):
    status = "failed"

class UnknownResult(CheckinError):
    status = "unknown"

class LoginRequired(CheckinError):
    status = "login_required"

def number(value):
    return type(value) in (int, float) and math.isfinite(value)

def parse_response(status, data):
    """把网站响应转换为本项目结果；字段依据公开前端，仍需真实账号验证。"""
    if status == 401:
        raise LoginRequired("登录失效，请运行 login 重新登录。")
    if not isinstance(data, dict):
        raise UnknownResult("响应格式异常，结果未知。")
    code = data.get("code")
    if code is not None and not isinstance(code, str):
        raise UnknownResult("网站业务状态字段异常，结果未知。")
    if code in {"no_token", "invalid_token", "token_revoked", "unauthorized"}:
        raise LoginRequired("登录失效，请运行 login 重新登录。")
    if code == "already_signed" and status in (200, 400, 409):
        # 重复签到不能证明之前的模式或奖励是多少，未知信息用 None，不能填 0。
        return {"status": "already_signed", "mode": None, "points_change": None,
                "balance": None, "multiplier": None}
    if status >= 500:
        raise UnknownResult("网站服务器异常，未确认签到结果。")
    if not 200 <= status < 300 or code not in (None, "ok"):
        raise CheckinError(f"网站拒绝操作（HTTP {status}），请在网页查看原因。")
    # 只校验是否为有效数值：运气签到的零分或负分同样属于成功结果。
    award, balance = data.get("award"), data.get("new_balance")
    if not number(award) or not number(balance):
        raise UnknownResult("响应缺少积分结果，签到状态未知。")
    mode = data.get("mode")
    multiplier = data.get("multiplier")
    return {"status": "success", "mode": mode if mode in ("normal", "lucky") else None,
            "points_change": award, "balance": balance,
            "multiplier": multiplier if number(multiplier) else None}

def interpret_response(status, data):
    result = parse_response(status, data)
    if result["status"] == "already_signed":
        return "今日已签到，无需重复操作。"
    text = f"签到成功：本次 {result['points_change']:+g} 积分，当前余额 {result['balance']:g}。"
    if result["multiplier"] is not None:
        text += f" 倍率 {result['multiplier']:g}。"
    return text

def response_data(response):
    try:
        return response.json()
    except (ValueError, PlaywrightError) as exc:
        raise UnknownResult("网站未返回有效 JSON，结果未知。") from exc

def response_match(path, method):
    # 网站自身会重试一次浏览器凭据错误；忽略首个中间响应，等其正常流程完成。
    proof_seen = False
    def matches(response):
        nonlocal proof_seen
        if (urlsplit(response.url).netloc != urlsplit(SITE).netloc
                or urlsplit(response.url).path != path or response.request.method != method):
            return False
        try:
            data = response.json()
        except (ValueError, PlaywrightError):
            return True
        if isinstance(data, dict) and data.get("code") in ("browser_proof_required", "browser_proof_invalid"):
            if not proof_seen:
                proof_seen = True
                return False
        return True
    return matches

def wait_for_signin_page(page, timeout_seconds, mode="normal"):
    button = page.get_by_role("button", name="普通签到" if mode == "normal" else "运气签到", exact=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if urlsplit(page.url).path == "/me/signin" and button.is_visible():
            return button
        if urlsplit(page.url).path == "/login":
            raise LoginRequired("尚未登录或登录已过期，请先运行 login。")
        page.wait_for_timeout(250)
    raise UnknownResult("签到页未就绪，可能遇到网络、人机验证或网页变化。")

def perform_checkin(page, timeout_seconds, mode="normal", structured=False):
    """点击所选签到按钮，以对应 POST 响应确认结果，而不是以点击成功确认。"""
    if mode not in ("normal", "lucky"):
        raise ValueError("无效签到模式")
    page.goto(SITE + "/me/signin", wait_until="domcontentloaded")
    button = wait_for_signin_page(page, timeout_seconds, mode)
    # 点击前注册响应监听，避免快速返回的请求在开始等待之前就已完成。
    with page.expect_response(response_match(SIGNIN_PATH, "POST"), timeout=timeout_seconds * 1000) as pending:
        button.click()
    response = pending.value
    data = response_data(response)
    if not structured:
        return interpret_response(response.status, data)
    result = parse_response(response.status, data)
    if result["status"] == "success" and result["mode"] is None:
        result["mode"] = mode
    return result

def parse_account(status, data):
    if status == 401 or (isinstance(data, dict) and data.get("code") in
                        ("no_token", "invalid_token", "token_revoked", "unauthorized")):
        raise LoginRequired("登录已失效，请运行 login。")
    if not 200 <= status < 300:
        raise CheckinError(f"无法读取账户（HTTP {status}）。")
    user = data.get("user") if isinstance(data, dict) else None
    if (not isinstance(user, dict) or type(user.get("id")) not in (str, int)
            or not str(user["id"]) or not number(user.get("points"))):
        raise UnknownResult("账户响应字段与预期不符，需要实测适配。")
    return {"account_id": str(user["id"]), "nickname": user.get("nickname"), "balance": user["points"]}

def fetch_account(page, timeout_seconds):
    observed = []
    verification_rejected = []
    matches = response_match("/api/portal/me", "GET")

    def observe(response):
        # 账户请求依赖浏览器验证；验证被拒绝时，页面可能只显示缓存资料。
        if (urlsplit(response.url).netloc == urlsplit(SITE).netloc
                and urlsplit(response.url).path in (
                    "/api/portal/auth/browser-challenge", "/api/portal/auth/browser-session")
                and response.status == 403):
            verification_rejected.append(True)
        if matches(response):
            observed.append(response)

    page.on("response", observe)
    try:
        page.goto(SITE + "/me", wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if observed:
                response = observed[-1]
                return parse_account(response.status, response_data(response))
            # 未登录用户可能先被前端跳转，整个过程不会发出 /me 接口请求。
            if urlsplit(page.url).path == "/login":
                raise LoginRequired("尚未登录或登录已过期，请运行 login。")
            page.wait_for_timeout(250)
        if verification_rejected:
            raise CheckinError("网站拒绝浏览器验证（HTTP 403），未能读取实时账户。请设置 browser.headless=false 后重启服务，并按页面要求完成验证。")
        raise UnknownResult("未收到账户响应，请检查网站或使用 --headed 查看。")
    finally:
        page.remove_listener("response", observe)

def save_login(page, timeout_seconds):
    """从环境变量自动登录；未配置凭证时等待人工登录，以服务端账户确认。"""
    email = os.environ.get("DIAN115_EMAIL", "")
    password = os.environ.get("DIAN115_PASSWORD", "")
    if bool(email) != bool(password):
        raise ValueError("请同时设置 DIAN115_EMAIL 和 DIAN115_PASSWORD，或都留空使用手动登录。")
    verified = []
    rejected = []
    def observe(response):
        if urlsplit(response.url).netloc != urlsplit(SITE).netloc:
            return
        if urlsplit(response.url).path not in {
            "/api/portal/me", "/api/portal/auth/login", "/api/portal/auth/tg/widget"
        }:
            return
        if urlsplit(response.url).path == "/api/portal/auth/login" and response.status in (400, 401):
            rejected.append(True)
        try:
            verified.append(parse_account(response.status, response_data(response)))
        except CheckinError:
            pass
    page.on("response", observe)
    try:
        page.goto(SITE + "/login?redirect=/me/signin", wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout_seconds
        if email and password and not verified:
            # 已登录的资料目录可能被页面直接跳转；这时不再输入密码。
            while time.monotonic() < deadline and not verified:
                field = page.locator('input[type="email"]')
                if field.is_visible():
                    field.fill(email)
                    page.locator('input[type="password"]').fill(password)
                    page.get_by_role("button", name="登录进入", exact=True).click()
                    break
                page.wait_for_timeout(250)
            print("已尝试自动登录；若页面要求验证，请在浏览器中完成。", flush=True)
        else:
            print(f"请在浏览器中登录并完成验证，最长等待 {timeout_seconds} 秒。", flush=True)
        while time.monotonic() < deadline:
            if verified:
                return verified[-1]
            if rejected:
                raise LoginRequired("网站拒绝账号密码登录，请检查 .env 中的凭证或在网页确认原因。")
            page.wait_for_timeout(250)
        raise LoginRequired("等待登录超时，未确认成功。")
    finally:
        page.remove_listener("response", observe)

class Browser:
    """每次操作独立创建并关闭浏览器；持久资料保留登录信息和网站浏览器密钥。"""
    def __init__(self, settings):
        self.settings = settings

    def execute(self, operation, mode=None, expected_account=None):
        from filelock import FileLock, Timeout
        cfg = self.settings
        cfg.profile.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 文件锁跨进程生效，防止单次命令和常驻服务同时打开同一浏览器资料。
            with FileLock(str(cfg.profile) + ".lock", timeout=0):
                with sync_playwright() as playwright:
                    options = {"channel": cfg.channel} if cfg.channel != "chromium" else {}
                    context = playwright.chromium.launch_persistent_context(
                        str(cfg.profile), headless=False if operation == "login" else cfg.headless,
                        viewport={"width": 1100, "height": 800}, locale="zh-CN",
                        accept_downloads=False, **options,
                    )
                    result = None
                    session_ready = False
                    try:
                        # 重新登录不恢复备份 Cookie，也可用于修复损坏的备份。
                        if operation != "login":
                            restore_cookies(context, cfg.profile / "session-cookies.json")
                        session_ready = True
                        context.set_default_timeout(cfg.timeout * 1000)
                        page = context.pages[0] if context.pages else context.new_page()
                        if operation == "login":
                            result = save_login(page, cfg.login_timeout)
                            return result
                        account = fetch_account(page, cfg.timeout)
                        if expected_account is not None and account["account_id"] != expected_account:
                            raise LoginRequired("浏览器账号与本地身份不符，请停止服务并重新运行 login。")
                        if operation == "account":
                            result = {"status": "ok", **account}
                            return result
                        result = perform_checkin(page, cfg.timeout, mode or cfg.mode, structured=True)
                        result["account_id"] = account["account_id"]
                        return result
                    finally:
                        try:
                            # Chromium 关闭后不保留会话 Cookie，需单独备份以支持下次运行。
                            # 这是登录资料，不是签到记录；恢复失败时不覆盖原备份。
                            from storage import atomic_json
                            if session_ready:
                                atomic_json(cfg.profile / "session-cookies.json", context.cookies())
                        except (OSError, ValueError, PlaywrightError):
                            if result is not None:
                                result["session_saved"] = False
                                result["message"] = "操作结果已获取，但浏览器登录资料保存失败。"
                        finally:
                            context.close()
        except Timeout:
            return {"status": "busy", "message": "浏览器正在使用中，请稍后重试。"}


def restore_cookies(context, path):
    """恢复尚未过期的 Cookie；expires=-1 表示会话 Cookie，也需要恢复。"""
    from storage import read_json
    try:
        cookies = read_json(path, [])
        if not isinstance(cookies, list):
            raise ValueError("cookie format")
        now = time.time()
        context.add_cookies([cookie for cookie in cookies if cookie.get("expires", -1) == -1
                            or cookie.get("expires", 0) > now])
    except (ValueError, AttributeError, PlaywrightError) as exc:
        raise UnknownResult("本地浏览器登录资料损坏，请检查 session-cookies.json 或重新登录。") from exc

if __name__ == "__main__":
    from app import main
    raise SystemExit(main())
