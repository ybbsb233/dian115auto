"""读取并校验 TOML 配置；配置不可变，加载过程不创建目录或写入文件。"""
from dataclasses import dataclass
from datetime import time
from pathlib import Path
import os
import tomllib
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    data: Path
    profile: Path
    zone: ZoneInfo
    at: time
    mode: str = "normal"
    channel: str = "chromium"
    headless: bool = True
    timeout: int = 45
    login_timeout: int = 300
    enabled: bool = True
    catch_up: bool = True
    retries: int = 2
    retry_seconds: int = 60
    host: str = "127.0.0.1"
    port: int = 8080
    api_key: str = ""


def load_settings(path):
    """相对路径以配置文件为基准；API 密钥从指定环境变量读取。"""
    path = Path(path).resolve()
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    # 拒绝拼错的配置项，避免用户以为设置生效而实际使用了默认值。
    allowed = {
        "schedule": {"enabled", "time", "timezone", "catch_up", "retries", "retry_seconds"},
        "checkin": {"mode"},
        "browser": {"channel", "profile", "headless", "timeout", "login_timeout"},
        "storage": {"directory"}, "api": {"host", "port", "key_env"},
    }
    for section, values in raw.items():
        if section not in allowed or not isinstance(values, dict) or set(values) - allowed[section]:
            raise ValueError(f"未知配置项：{section}")

    def get(section, key, default, kind):
        value = raw.get(section, {}).get(key, default)
        # 精确比较类型：Python 的 bool 是 int 子类，但不能作为重试次数等参数。
        if type(value) is not kind:
            raise ValueError(f"{section}.{key} 类型错误")
        return value

    def integer(section, key, default, low, high):
        value = get(section, key, default, int)
        if not low <= value <= high:
            raise ValueError(f"{section}.{key} 必须在 {low}..{high} 之间")
        return value

    at_text = get("schedule", "time", "09:00", str)
    if len(at_text) != 5 or at_text[2] != ":":
        raise ValueError("schedule.time 必须为 HH:MM")
    at = time.fromisoformat(at_text)
    mode = get("checkin", "mode", "normal", str)
    channel = get("browser", "channel", "chromium", str)
    if mode not in ("normal", "lucky") or channel not in ("chromium", "chrome", "msedge"):
        raise ValueError("签到模式或浏览器类型错误")
    host = get("api", "host", "127.0.0.1", str)
    key = os.environ.get(get("api", "key_env", "DIAN115_API_KEY", str), "")
    if host not in ("127.0.0.1", "localhost", "::1") and len(key) < 24:
        raise ValueError("对外监听需要设置至少 24 字符的 API 环境变量密钥")
    return Settings(
        data=(path.parent / get("storage", "directory", "data", str)).resolve(),
        profile=(path.parent / get("browser", "profile", ".state/browser", str)).resolve(),
        zone=ZoneInfo(get("schedule", "timezone", "Asia/Shanghai", str)), at=at,
        mode=mode, channel=channel, host=host, api_key=key,
        port=integer("api", "port", 8080, 1, 65535),
        timeout=integer("browser", "timeout", 45, 1, 300),
        login_timeout=integer("browser", "login_timeout", 300, 1, 3600),
        headless=get("browser", "headless", True, bool),
        enabled=get("schedule", "enabled", True, bool),
        catch_up=get("schedule", "catch_up", True, bool),
        retries=integer("schedule", "retries", 2, 0, 10),
        retry_seconds=integer("schedule", "retry_seconds", 60, 1, 86400),
    )
