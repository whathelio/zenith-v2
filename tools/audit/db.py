import sqlite3
import os

DB = r"D:\下载文件\新建文件夹\zenith-v2\data\zenith.db"
print(f"DB: {DB}")
print(f"文件大小: {os.path.getsize(DB)/1024/1024:.2f} MB\n")

c = sqlite3.connect(DB)
cur = c.cursor()
cur.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")
tabs = [r[0] for r in cur.fetchall()]

# 找每张表的时间列
def time_col(t):
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    for cand in ("created_at", "updated_at", "recorded_at", "start_time", "timestamp", "date", "ts"):
        if cand in cols:
            return cand
    return None

print(f"{'表名':<26}{'行数':>8}   最新时间")
print("-" * 78)
rows_report = []
for t in tabs:
    try:
        cur.execute(f"select count(*) from {t}")
        n = cur.fetchone()[0]
    except Exception as e:
        print(f"{t:<26}  ERR {e}")
        continue
    tc = time_col(t)
    latest = "-"
    if tc and n:
        try:
            cur.execute(f"select max({tc}) from {t}")
            latest = str(cur.fetchone()[0])[:19]
        except Exception:
            pass
    rows_report.append((t, n, latest))
    print(f"{t:<26}{n:>8}   {latest}")

print("\n=== 空表（0 行）===")
empty = [t for t, n, _ in rows_report if n == 0]
print("  ", ", ".join(empty) if empty else "（无）")

print("\n=== 超过 30 天未更新的表 ===")
import datetime
now = datetime.datetime.now()
for t, n, latest in rows_report:
    if n and latest and latest != "-":
        try:
            d = datetime.datetime.fromisoformat(latest.replace("Z", ""))
            age = (now - d).days
            if age > 30:
                print(f"   {t:<26} {n:>8} 行   最后更新 {latest[:10]}（{age} 天前）")
        except Exception:
            pass
