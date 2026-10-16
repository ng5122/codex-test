"""SQLite persistence for Red Potato Radar."""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def data_dir(read_only=False) -> Path:
    override = os.environ.get("RED_POTATO_RADAR_HOME")
    base = Path(override).expanduser() if override else Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    if base.is_file() or base.suffix.lower() == ".exe" or base.suffix == ".py":
        base = base.parent
    if read_only:
        if (base/"monitor.db").is_file():return base
        fallback=Path.home()/"Documents"/"RedPotatoRadar"
        if (fallback/"monitor.db").is_file():return fallback
        return base
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write-test"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        return base
    except OSError:
        fallback = Path.home() / "Documents" / "RedPotatoRadar"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class Database:
    def __init__(self, path: str | Path | None = None, *, read_only: bool = False):
        self.path = Path(path) if path else data_dir(read_only=read_only) / "monitor.db"
        self.read_only=read_only
        if read_only:
            if not self.path.is_file():raise FileNotFoundError(f"本地数据库不存在：{self.path}")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.initialize()

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            conn=sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True,timeout=15,check_same_thread=False,factory=_ClosingConnection)
        else:
            conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, shop_name TEXT, shop_id TEXT,
                    cover TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT,
                    last_error TEXT, delisted_at TEXT, delisted_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    product_id TEXT NOT NULL, captured_at TEXT NOT NULL, sold INTEGER NOT NULL,
                    shop_sold INTEGER, price REAL, fans INTEGER, stock_status INTEGER, deliverable INTEGER,
                    PRIMARY KEY(product_id,captured_at),
                    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, level TEXT NOT NULL,
                    source TEXT, product_id TEXT, title TEXT, message TEXT, details TEXT
                );
                CREATE TABLE IF NOT EXISTS deleted_products (id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
            """)
            self._migrate(c)
            c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(captured_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_product_time ON snapshots(product_id,captured_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(time)")

    @staticmethod
    def _migrate(c: sqlite3.Connection):
        additions = {
            "products": {"title": "TEXT NOT NULL DEFAULT ''", "shop_name": "TEXT", "shop_id": "TEXT", "cover": "TEXT", "active": "INTEGER NOT NULL DEFAULT 1", "created_at": "TEXT", "last_error": "TEXT", "delisted_at": "TEXT", "delisted_reason": "TEXT"},
            "snapshots": {"product_id": "TEXT", "captured_at": "TEXT", "sold": "INTEGER NOT NULL DEFAULT 0", "shop_sold": "INTEGER", "price": "REAL", "fans": "INTEGER", "stock_status": "INTEGER", "deliverable": "INTEGER"},
            "logs": {"time": "TEXT", "level": "TEXT NOT NULL DEFAULT 'info'", "source": "TEXT", "product_id": "TEXT", "title": "TEXT", "message": "TEXT", "details": "TEXT"},
        }
        for table, fields in additions.items():
            existing = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
            for name, typ in fields.items():
                if name not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        log_columns={row[1] for row in c.execute("PRAGMA table_info(logs)")}
        if "id" not in log_columns:
            c.execute("ALTER TABLE logs ADD COLUMN id INTEGER")
            c.execute("UPDATE logs SET id=rowid WHERE id IS NULL")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_logs_id ON logs(id)")
            c.execute("""CREATE TRIGGER IF NOT EXISTS trg_logs_id AFTER INSERT ON logs WHEN NEW.id IS NULL
                BEGIN UPDATE logs SET id=COALESCE((SELECT MAX(id) FROM logs WHERE id IS NOT NULL AND rowid!=NEW.rowid),0)+1 WHERE rowid=NEW.rowid; END""")
        # Add the required composite uniqueness to legacy tables that predate this constraint.
        c.execute("DELETE FROM snapshots WHERE product_id IS NOT NULL AND captured_at IS NOT NULL AND rowid NOT IN (SELECT MAX(rowid) FROM snapshots WHERE product_id IS NOT NULL AND captured_at IS NOT NULL GROUP BY product_id,captured_at)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshots_product_time ON snapshots(product_id,captured_at)")

    def upsert_product(self, item: dict):
        with self.transaction() as c:
            c.execute("""INSERT INTO products(id,title,shop_name,shop_id,cover,active,created_at)
                VALUES(?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                shop_name=excluded.shop_name,shop_id=excluded.shop_id,cover=excluded.cover,active=1""",
                (item["id"], item.get("title") or item["id"], item.get("shop_name"), item.get("shop_id"), item.get("cover"), now_iso()))

    def save_snapshot(self, product_id: str, captured_at: str, item: dict):
        with self.transaction() as c:
            c.execute("""INSERT INTO snapshots(product_id,captured_at,sold,shop_sold,price,fans,stock_status,deliverable)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(product_id,captured_at) DO UPDATE SET
                sold=excluded.sold,shop_sold=excluded.shop_sold,price=excluded.price,fans=excluded.fans,
                stock_status=excluded.stock_status,deliverable=excluded.deliverable""",
                (product_id,captured_at,item["sold"],item.get("shop_sold"),item.get("price"),item.get("fans"),item.get("stock_status"),item.get("deliverable")))
            c.execute("UPDATE products SET last_error=NULL WHERE id=?", (product_id,))

    def log(self, level: str, message: str, source="app", product_id=None, title=None, details=None):
        with self.connect() as c:
            c.execute("INSERT INTO logs(time,level,source,product_id,title,message,details) VALUES(?,?,?,?,?,?,?)",
                      (now_iso(),level,source,product_id,title,message,details))

    def active_products(self):
        with self.connect() as c:
            return [dict(x) for x in c.execute("SELECT * FROM products WHERE active=1 ORDER BY created_at,id")]

    def record_error(self, product_id: str, message: str, details: str = ""):
        with self.transaction() as c:
            c.execute("UPDATE products SET last_error=? WHERE id=?", (message,product_id))
            title=c.execute("SELECT title FROM products WHERE id=?",(product_id,)).fetchone()
            c.execute("INSERT INTO logs(time,level,source,product_id,title,message,details) VALUES(?,?,?,?,?,?,?)",
                      (now_iso(),"error","collector",product_id,title[0] if title else None,message,details))

    def delist(self, product_id: str, reason: str):
        with self.transaction() as c:
            c.execute("UPDATE products SET active=0,last_error=NULL,delisted_at=?,delisted_reason=? WHERE id=?",(now_iso(),reason,product_id))

    def deactivate(self, product_id: str):
        with self.transaction() as c:
            c.execute("UPDATE products SET active=0,last_error=NULL,delisted_at=NULL,delisted_reason=NULL WHERE id=?",(product_id,))

    def delete_product(self, product_id: str):
        with self.transaction() as c:
            c.execute("INSERT OR REPLACE INTO deleted_products(id,deleted_at) VALUES(?,?)",(product_id,now_iso()))
            c.execute("DELETE FROM products WHERE id=?",(product_id,))

    def snapshot_count(self):
        with self.connect() as c:
            return c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    def cleanup(self, snapshot_days=90, log_days=30):
        snapshot_cutoff=(datetime.now(TZ)-timedelta(days=snapshot_days)).replace(microsecond=0).isoformat()
        log_cutoff=(datetime.now(TZ)-timedelta(days=log_days)).replace(microsecond=0).isoformat()
        with self.transaction() as c:
            before = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            c.execute("""DELETE FROM snapshots WHERE captured_at < ? AND captured_at !=
                (SELECT MAX(s2.captured_at) FROM snapshots s2 WHERE s2.product_id=snapshots.product_id AND s2.captured_at < ?)""",
                (snapshot_cutoff,snapshot_cutoff))
            snaps = before - c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            logs_before = c.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            c.execute("DELETE FROM logs WHERE time < ?",(log_cutoff,))
            logs = logs_before - c.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        with self.connect() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")
        return {"snapshots":snaps,"logs":logs,"bytes":self.path.stat().st_size}


class _ClosingConnection(sqlite3.Connection):
    """Make `with db.connect()` release the file handle as well as commit/rollback."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type,exc,tb)
        finally:
            self.close()
