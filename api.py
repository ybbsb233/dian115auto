"""将固定的账户及签到操作封装为 JSON API，不接受任意网址或浏览器指令。"""
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import socket
from urllib.parse import parse_qs, urlsplit
from storage import RecordError

LOG = logging.getLogger("dian115.service")


def http_status(result):
    """映射网站操作的结果；本地状态查询中的 unknown 仍应返回 HTTP 200。"""
    return {"login_required": 401, "busy": 409, "unknown": 502, "failed": 502}.get(result.get("status"), 200)


def make_server(engine):
    cfg = engine.cfg

    class Handler(BaseHTTPRequestHandler):
        server_version = "Dian115/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # 关闭默认访问日志，避免请求路径、参数或凭据意外进入日志。

        def reply(self, status, body):
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self):
            if cfg.api_key:
                actual = self.headers.get("Authorization", "").encode("utf-8")
                expected = ("Bearer " + cfg.api_key).encode("utf-8")
                if not hmac.compare_digest(actual, expected):
                    self.reply(401, {"status": "unauthorized", "message": "需要 API 访问密钥。"})
                    return False
            else:
                # 无密钥的本机服务拒绝跨域来源及异常 Host，降低恶意网页调用风险。
                host = urlsplit("http://" + self.headers.get("Host", "")).hostname
                if host not in ("127.0.0.1", "localhost", "::1") or self.headers.get("Origin"):
                    self.reply(403, {"status": "forbidden"})
                    return False
            return True

        def dispatch(self, method):
            try:
                if not self.authorized():
                    return
                target = urlsplit(self.path)
                path = target.path
                if method == "GET":
                    if path == "/health":
                        worker = getattr(self.server, "scheduler_thread", None)
                        result = {"status": "ok", "schedule_enabled": cfg.enabled,
                                  "scheduler_alive": bool(worker and worker.is_alive())}
                    elif path == "/account":
                        result = engine.account()
                    elif path == "/checkin/status":
                        result = engine.status()
                    elif path == "/checkin/history":
                        params = parse_qs(target.query, keep_blank_values=True, max_num_fields=10)
                        if set(params) - {"since", "until", "page", "size"} or any(len(v) != 1 for v in params.values()):
                            raise ValueError("查询参数错误")
                        since, until = [params.get(k, [None])[0] for k in ("since", "until")]
                        for day in (since, until):
                            if day is not None and date.fromisoformat(day).isoformat() != day:
                                raise ValueError("日期必须为 YYYY-MM-DD")
                        if since and until and since > until:
                            raise ValueError("since 不能晚于 until")
                        page, size = int(params.get("page", ["1"])[0]), int(params.get("size", ["20"])[0])
                        if page < 1 or not 1 <= size <= 100:
                            raise ValueError("page 必须大于 0，size 为 1..100")
                        result = engine.store.history(since, until, page, size)
                    else:
                        self.reply(404, {"status": "not_found"})
                        return
                    # 本地“未知”是正常的查询结果，不表示这次 HTTP 查询失败。
                    self.reply(http_status(result) if path == "/account" else 200, result)
                elif method == "POST" and path == "/checkin":
                    if target.query:
                        raise ValueError("签到参数请放在 JSON 请求体中")
                    if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                        self.reply(415, {"status": "bad_request", "message": "需要 application/json"})
                        return
                    if self.headers.get("Transfer-Encoding"):
                        raise ValueError("不支持分块请求体")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 4096:
                        raise ValueError("请求体过大")
                    data = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(data, dict) or set(data) - {"mode"}:
                        raise ValueError("仅接受 mode 字段")
                    mode = data.get("mode", cfg.mode)
                    if mode not in ("normal", "lucky"):
                        raise ValueError("mode 必须为 normal 或 lucky")
                    result = engine.checkin(mode)
                    self.reply(http_status(result), result)
                else:
                    self.reply(404, {"status": "not_found"})
            except RecordError:
                LOG.error("本地记录损坏，未进行签到操作")
                self.reply(500, {"status": "storage_error", "message": "本地记录损坏，请检查数据目录。"})
            except (ValueError, UnicodeError) as exc:
                # Constant text avoids echoing malformed payloads into logs/responses.
                LOG.warning("API 请求或本地数据解析失败（%s）", type(exc).__name__)
                self.reply(400, {"status": "bad_request", "message": "参数或数据格式错误。"})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                LOG.warning("API 客户端断开连接；已完成的签到仍按正常流程保存。")
            except OSError:
                LOG.error("API 本地存储或网络错误")
                self.reply(500, {"status": "storage_error"})
            except Exception:
                LOG.error("API 内部异常，未向客户端暴露详细信息")
                self.reply(500, {"status": "internal_error"})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

    class Server(ThreadingHTTPServer):
        daemon_threads = False  # 关闭服务时等待请求线程，避免中断签到结果的落盘。
        address_family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET

        def handle_error(self, request, client_address):
            LOG.error("HTTP 连接处理异常")

    return Server((cfg.host, cfg.port), Handler)
