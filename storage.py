"""本地 JSON 存储：原子替换记录；缺少确认记录只代表未知，不代表未签到。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile


class RecordError(ValueError):
    pass


def read_json(path, default=None):
    """仅文件不存在时使用默认值；损坏文件必须报错，不能当作没有记录。"""
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeError) as exc:
        raise RecordError("本地 JSON 文件损坏") from exc


def atomic_json(path, data):
    """先在同目录写完并刷新临时文件，再替换目标，避免读到半份 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    """按账号隔离每日确认记录；异常尝试留在服务日志，不作为签到记录。"""
    def __init__(self, directory, account_id):
        self.account_id = account_id
        # 哈希用于生成稳定且安全的目录名，不是对账号信息加密。
        digest = hashlib.sha256(account_id.encode()).hexdigest()
        self.directory = Path(directory) / "checkins" / digest

    def path(self, day):
        from datetime import date
        day = date.fromisoformat(day).isoformat()
        return self.directory / (day + ".json")

    def get(self, day):
        result = read_json(self.path(day))
        if result is not None and (not isinstance(result, dict)
                or result.get("account_id") != self.account_id or result.get("date") != day
                or result.get("status") not in ("success", "already_signed")):
            raise RecordError("本地签到记录格式错误，请检查数据目录")
        return result

    def status(self, day):
        record = self.get(day)
        return {"status": "signed" if record else "unknown", "date": day,
                "account_id": self.account_id, "record": record, "source": "local"}

    def save(self, record):
        if record.get("account_id") != self.account_id:
            raise ValueError("记录账号不匹配")
        if record["status"] not in ("success", "already_signed"):
            return
        old = self.get(record["date"])
        # 重复签到响应可能不含奖励信息，不能用它覆盖已知积分；失败也不落盘。
        if old and (old["status"] == "success" or record["status"] == "already_signed"):
            return
        atomic_json(self.path(record["date"]), record)

    def history(self, since=None, until=None, page=1, size=20):
        """日期文件按新到旧分页，范围包含起止日；不包含 schedule.json。"""
        files = sorted(self.directory.glob("????-??-??.json"), reverse=True)
        files = [p for p in files if (since is None or p.stem >= since) and (until is None or p.stem <= until)]
        return {"account_id": self.account_id, "total": len(files), "page": page, "size": size,
                "items": [self.get(p.stem) for p in files[(page-1)*size:page*size]]}
