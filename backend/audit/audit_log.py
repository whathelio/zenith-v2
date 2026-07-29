"""Audit Log — 防篡改 JSONL + SHA256 Hash 链"""
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

AUDIT_DIR = Path(__file__).parent.parent.parent / "data" / "audit"

logger = logging.getLogger("zenith.audit")


def _today_file() -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"{today}.jsonl"


def _compute_hash(prev_hash: str, timestamp: str, event_type: str, data: dict) -> str:
    raw = f"{prev_hash}{timestamp}{event_type}{json.dumps(data, sort_keys=True, ensure_ascii=False)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def log_event(event_type: str, data: dict, conv_id: str = ""):
    """写入一条审计日志到 JSONL 文件，附加 hash 链"""
    try:
        tz = timezone(timedelta(hours=8))
        timestamp = datetime.now(tz).isoformat()

        # 读取上一条记录的 hash 作为 prev_hash
        file_path = _today_file()
        prev_hash = "0" * 16
        if file_path.exists():
            try:
                lines = file_path.read_text(encoding="utf-8").strip().split("\n")
                if lines:
                    last = json.loads(lines[-1])
                    prev_hash = last.get("hash", prev_hash)
            except Exception:
                pass

        entry = {
            "timestamp": timestamp,
            "event_type": event_type,
            "conv_id": conv_id,
            "prev_hash": prev_hash,
            "data": data,
        }
        entry["hash"] = _compute_hash(prev_hash, timestamp, event_type, data)

        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    except Exception as e:
        logger.warning("审计日志写入失败: %s", e)


def verify_chain(date: str) -> dict:
    """验证指定日期日志的 hash 链完整性"""
    file_path = AUDIT_DIR / f"{date}.jsonl"
    if not file_path.exists():
        return {"valid": False, "error": f"日志文件不存在: {date}.jsonl"}

    try:
        lines = file_path.read_text(encoding="utf-8").strip().split("\n")
        if not lines:
            return {"valid": True, "entries": 0}

        prev = "0" * 16
        for i, line in enumerate(lines):
            entry = json.loads(line)
            expected = _compute_hash(
                prev, entry["timestamp"], entry["event_type"], entry["data"]
            )
            if entry.get("hash") != expected:
                return {
                    "valid": False,
                    "error": f"Hash 断裂于第 {i+1} 行",
                    "entry_timestamp": entry.get("timestamp"),
                    "expected_hash": expected,
                    "actual_hash": entry.get("hash"),
                }
            prev = expected

        return {"valid": True, "entries": len(lines)}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def export_logs(conv_id: str = "", date: str = "") -> list[dict]:
    """导出审计日志，可按对话 ID 或日期过滤"""
    results = []

    if date:
        files = [AUDIT_DIR / f"{date}.jsonl"]
    else:
        files = sorted(AUDIT_DIR.glob("*.jsonl"), reverse=True)

    for fp in files:
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if conv_id and entry.get("conv_id") != conv_id:
                    continue
                results.append(entry)
            except json.JSONDecodeError:
                continue

    return results
