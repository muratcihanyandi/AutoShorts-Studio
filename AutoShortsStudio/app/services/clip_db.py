import sqlite3
import os
import json
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional
from loguru import logger

from app.utils import utils
from app.models.clip_schema import (
    StreamerTarget, ClipItem, ClipStatus, PlatformType, ClipPipelineSettings,
    ClipTargetFormat, ClipSourceKind,
)


# update_clip_status'in kabul ettigi kolonlar (SQL enjeksiyonuna karsi beyaz liste)
_CLIPS_UPDATE_COLUMNS = {
    "raw_video_path", "processed_video_path", "subtitle_path", "transcript",
    "hype_score", "viral_score", "hook_text", "cut_start_sec", "cut_end_sec", "priority",
    "youtube_title", "youtube_description", "youtube_tags", "youtube_video_id",
    "youtube_status", "published_at", "target_format", "source_kind", "source_key",
    "error_message",
}


class ClipDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            storage_dir = os.path.join(utils.root_dir(), "storage")
            os.makedirs(storage_dir, exist_ok=True)
            db_path = os.path.join(storage_dir, "clips.db")
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Yayinci hedefleri tablosu
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS streamers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    display_name TEXT,
                    is_active INTEGER DEFAULT 1,
                    formats TEXT,
                    min_views INTEGER DEFAULT 100,
                    max_duration_seconds INTEGER DEFAULT 60,
                    check_interval_minutes INTEGER DEFAULT 60,
                    fan_channel_queries TEXT,
                    last_checked_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(platform, channel_name)
                )
            """)

            # 2. Islenen klipler tablosu
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_id TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    duration REAL DEFAULT 0,
                    view_count INTEGER DEFAULT 0,
                    thumbnail_url TEXT,
                    created_at_platform TEXT,
                    status TEXT NOT NULL,
                    raw_video_path TEXT,
                    processed_video_path TEXT,
                    subtitle_path TEXT,
                    hype_score REAL DEFAULT 0,
                    viral_score INTEGER DEFAULT 0,
                    hook_text TEXT,
                    cut_start_sec REAL DEFAULT 0,
                    cut_end_sec REAL DEFAULT 0,
                    youtube_title TEXT,
                    youtube_description TEXT,
                    youtube_tags TEXT,
                    youtube_video_id TEXT,
                    youtube_status TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 3. Genel ayarlar tablosu
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            self._migrate(cursor)
            conn.commit()
            logger.info(f"ClipDatabase initialized at {self.db_path}")

    def _migrate(self, cursor):
        """Eski surum clips.db dosyalarina yeni kolonlari guvenli sekilde ekler."""
        def existing_columns(table: str) -> set:
            cursor.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}

        clips_cols = existing_columns("clips")
        clips_migration = {
            "target_format": "ALTER TABLE clips ADD COLUMN target_format TEXT DEFAULT 'shorts'",
            "source_kind": "ALTER TABLE clips ADD COLUMN source_kind TEXT DEFAULT 'clip'",
            "source_key": "ALTER TABLE clips ADD COLUMN source_key TEXT",
            "transcript": "ALTER TABLE clips ADD COLUMN transcript TEXT",
            "priority": "ALTER TABLE clips ADD COLUMN priority INTEGER DEFAULT 0",
            "published_at": "ALTER TABLE clips ADD COLUMN published_at TEXT",
            "hook_text": "ALTER TABLE clips ADD COLUMN hook_text TEXT",
            "viral_score": "ALTER TABLE clips ADD COLUMN viral_score INTEGER DEFAULT 0",
        }
        for col, sql in clips_migration.items():
            if col not in clips_cols:
                cursor.execute(sql)
                logger.info(f"ClipDatabase migration: clips +{col}")

        streamer_cols = existing_columns("streamers")
        if "formats" not in streamer_cols:
            cursor.execute(
                "ALTER TABLE streamers ADD COLUMN formats TEXT "
                "DEFAULT '[\"shorts\", \"horizontal\"]'"
            )
            logger.info("ClipDatabase migration: streamers +formats")

        if "fan_channel_queries" not in streamer_cols:
            cursor.execute(
                "ALTER TABLE streamers ADD COLUMN fan_channel_queries TEXT DEFAULT '[]'"
            )
            logger.info("ClipDatabase migration: streamers +fan_channel_queries")

    # ================= Yayinci Yonetimi =================

    def add_streamer(self, streamer: StreamerTarget) -> int:
        formats_json = json.dumps([f.value if isinstance(f, ClipTargetFormat) else str(f)
                                   for f in streamer.formats])
        fan_queries_json = json.dumps(streamer.fan_channel_queries or [])
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.utcnow().isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO streamers
                (platform, channel_name, display_name, is_active, formats, min_views,
                 max_duration_seconds, check_interval_minutes, fan_channel_queries, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                streamer.platform.value,
                streamer.channel_name.strip().lower(),
                streamer.display_name or streamer.channel_name,
                1 if streamer.is_active else 0,
                formats_json,
                streamer.min_views,
                streamer.max_duration_seconds,
                streamer.check_interval_minutes,
                fan_queries_json,
                now_str
            ))
            conn.commit()
            return cursor.lastrowid

    def list_streamers(self, active_only: bool = False) -> List[StreamerTarget]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM streamers"
            if active_only:
                query += " WHERE is_active = 1"
            cursor.execute(query)
            rows = cursor.fetchall()

            result = []
            for r in rows:
                fan_queries = []
                if "fan_channel_queries" in r.keys() and r["fan_channel_queries"]:
                    try:
                        fan_queries = json.loads(r["fan_channel_queries"])
                    except Exception:
                        fan_queries = []
                result.append(StreamerTarget(
                    id=r["id"],
                    platform=PlatformType(r["platform"]),
                    channel_name=r["channel_name"],
                    display_name=r["display_name"],
                    is_active=bool(r["is_active"]),
                    formats=self._parse_formats(r["formats"]),
                    min_views=r["min_views"],
                    max_duration_seconds=r["max_duration_seconds"],
                    check_interval_minutes=r["check_interval_minutes"],
                    fan_channel_queries=fan_queries,
                    last_checked_at=datetime.fromisoformat(r["last_checked_at"]) if r["last_checked_at"] else None,
                    created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
                ))
            return result

    @staticmethod
    def _parse_formats(raw) -> List[ClipTargetFormat]:
        if not raw:
            return [ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL]
        try:
            values = json.loads(raw)
            return [ClipTargetFormat(v) for v in values]
        except Exception:
            return [ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL]

    def delete_streamer(self, streamer_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM streamers WHERE id = ?", (streamer_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_streamer_checked(self, platform: str, channel_name: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE streamers SET last_checked_at = ?
                WHERE platform = ? AND channel_name = ?
            """, (datetime.utcnow().isoformat(), platform, channel_name.strip().lower()))
            conn.commit()

    # ================= Klip Yonetimi =================

    def is_clip_processed(self, clip_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, status FROM clips WHERE clip_id = ?", (clip_id,))
            row = cursor.fetchone()
            if not row:
                return False
            # Failed ise tekrar denenebilir, diger durumlarda islenmis sayilir
            return row["status"] != ClipStatus.FAILED.value

    def get_clip_by_id(self, clip_id: str) -> Optional[ClipItem]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM clips WHERE clip_id = ?", (clip_id,))
            r = cursor.fetchone()
            if not r:
                return None
            return self._row_to_clip(r)

    def save_clip(self, clip: ClipItem) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.utcnow().isoformat()
            tags_json = json.dumps(clip.youtube_tags or [])
            published_at_str = clip.published_at.isoformat() if clip.published_at else None
            cursor.execute("""
                INSERT OR REPLACE INTO clips (
                    clip_id, platform, channel_name, title, url, duration, view_count,
                    thumbnail_url, created_at_platform, target_format, source_kind, source_key,
                    status, raw_video_path, processed_video_path, subtitle_path, transcript,
                    hype_score, viral_score, hook_text, cut_start_sec, cut_end_sec, priority,
                    youtube_title, youtube_description, youtube_tags, youtube_video_id,
                    youtube_status, published_at, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                clip.clip_id,
                clip.platform.value,
                clip.channel_name,
                clip.title,
                clip.url,
                clip.duration,
                clip.view_count,
                clip.thumbnail_url,
                clip.created_at_platform,
                clip.target_format.value,
                clip.source_kind.value,
                clip.source_key or clip.clip_id,
                clip.status.value,
                clip.raw_video_path,
                clip.processed_video_path,
                clip.subtitle_path,
                clip.transcript,
                clip.hype_score,
                clip.viral_score,
                clip.hook_text,
                clip.cut_start_sec,
                clip.cut_end_sec,
                clip.priority,
                clip.youtube_title,
                clip.youtube_description,
                tags_json,
                clip.youtube_video_id,
                clip.youtube_status,
                published_at_str,
                clip.error_message,
                clip.created_at.isoformat() if clip.created_at else now_str,
                now_str
            ))
            conn.commit()
            return cursor.lastrowid

    def update_clip_status(self, clip_id: str, status: ClipStatus, error_message: Optional[str] = None, **kwargs):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.utcnow().isoformat()
            updates = ["status = ?", "updated_at = ?"]
            params = [status.value, now_str]
            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)
            for k, v in kwargs.items():
                if k not in _CLIPS_UPDATE_COLUMNS:
                    raise ValueError(f"Invalid clip update column: {k}")
                if k == "youtube_tags" and isinstance(v, list):
                    v = json.dumps(v)
                if k == "published_at" and hasattr(v, "isoformat"):
                    v = v.isoformat()
                updates.append(f"{k} = ?")
                params.append(v)
            params.append(clip_id)
            sql = f"UPDATE clips SET {', '.join(updates)} WHERE clip_id = ?"
            cursor.execute(sql, params)
            conn.commit()

    def list_clips(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        target_format: Optional[str] = None,
    ) -> List[ClipItem]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM clips"
            conditions, params = [], []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if target_format:
                conditions.append("target_format = ?")
                params.append(target_format)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_clip(r) for r in rows]

    def count_clips(self, status: Optional[str] = None, target_format: Optional[str] = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT COUNT(*) AS cnt FROM clips"
            conditions, params = [], []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if target_format:
                conditions.append("target_format = ?")
                params.append(target_format)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            cursor.execute(query, params)
            return int(cursor.fetchone()["cnt"])

    def published_today_count(self, target_format: str) -> int:
        """Bugun (yerel saat) yayinlanan verilen formatdaki klip sayisi."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM clips
                WHERE status = ? AND target_format = ?
                  AND date(published_at) = date('now', 'localtime')
            """, (ClipStatus.PUBLISHED.value, target_format))
            return int(cursor.fetchone()["cnt"])

    def next_discovered_clip(self, target_format: str) -> Optional[ClipItem]:
        """Stokta islenmeyi bekleyen bir sonraki klip (populerligi yuksek oncelikli)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM clips
                WHERE status = ? AND target_format = ?
                ORDER BY view_count DESC, id ASC
                LIMIT 1
            """, (ClipStatus.DISCOVERED.value, target_format))
            r = cursor.fetchone()
            return self._row_to_clip(r) if r else None

    def next_ready_clip(self, target_format: str) -> Optional[ClipItem]:
        """Yayinlanmayi bekleyen en uygun READY klip (oncelik > hype > eskilik)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM clips
                WHERE status = ? AND target_format = ?
                ORDER BY priority DESC, hype_score DESC, id ASC
                LIMIT 1
            """, (ClipStatus.READY.value, target_format))
            r = cursor.fetchone()
            return self._row_to_clip(r) if r else None

    def delete_clip(self, clip_id: str) -> bool:
        clip = self.get_clip_by_id(clip_id)
        if clip:
            for file_path in [clip.raw_video_path, clip.processed_video_path, clip.subtitle_path]:
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted physical clip file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Could not remove physical file {file_path}: {e}")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM clips WHERE clip_id = ?", (clip_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clip_failed_recently(self, clip_id: str, hours: int = 6) -> bool:
        """Klip son X saat icinde FAILED olduysa True (kalici hatali icerigi tekrar denememek icin)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, updated_at FROM clips WHERE clip_id = ?",
                (clip_id,)
            )
            row = cursor.fetchone()
            if not row or row["status"] != ClipStatus.FAILED.value or not row["updated_at"]:
                return False
            try:
                failed_at = datetime.fromisoformat(row["updated_at"])
                return (datetime.utcnow() - failed_at).total_seconds() < hours * 3600
            except Exception:
                return False

    def source_siblings_pending(self, source_key: str, exclude_clip_id: str) -> bool:
        """
        Ayni kaynagi (source_key) paylasan ve islemi henuz bitmemis baska kayit var mi?
        Ham dosya silinmeden once kontrol icin kullanilir.
        """
        terminal = [ClipStatus.READY.value, ClipStatus.PUBLISHED.value,
                    ClipStatus.SPLIT.value, ClipStatus.SKIPPED.value]
        placeholders = ", ".join("?" for _ in terminal)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(*) AS cnt FROM clips
                WHERE source_key = ? AND clip_id != ?
                  AND status NOT IN ({placeholders})
            """, [source_key, exclude_clip_id] + terminal)
            return int(cursor.fetchone()["cnt"]) > 0

    def _row_to_clip(self, r: sqlite3.Row) -> ClipItem:
        tags = []
        if r["youtube_tags"]:
            try:
                tags = json.loads(r["youtube_tags"])
            except Exception:
                tags = []
        return ClipItem(
            id=r["id"],
            clip_id=r["clip_id"],
            platform=PlatformType(r["platform"]),
            channel_name=r["channel_name"],
            title=r["title"],
            url=r["url"],
            duration=r["duration"],
            view_count=r["view_count"],
            thumbnail_url=r["thumbnail_url"],
            created_at_platform=r["created_at_platform"],
            target_format=ClipTargetFormat(r["target_format"] or ClipTargetFormat.SHORTS.value),
            source_kind=ClipSourceKind(r["source_kind"] or ClipSourceKind.CLIP.value),
            source_key=r["source_key"] or r["clip_id"],
            status=ClipStatus(r["status"]),
            raw_video_path=r["raw_video_path"],
            processed_video_path=r["processed_video_path"],
            subtitle_path=r["subtitle_path"],
            transcript=r["transcript"],
            hype_score=r["hype_score"],
            viral_score=r["viral_score"] if "viral_score" in r.keys() and r["viral_score"] is not None else 0,
            hook_text=r["hook_text"] if "hook_text" in r.keys() else None,
            cut_start_sec=r["cut_start_sec"],
            cut_end_sec=r["cut_end_sec"],
            priority=r["priority"] or 0,
            youtube_title=r["youtube_title"],
            youtube_description=r["youtube_description"],
            youtube_tags=tags,
            youtube_video_id=r["youtube_video_id"],
            youtube_status=r["youtube_status"],
            published_at=datetime.fromisoformat(r["published_at"]) if r["published_at"] else None,
            error_message=r["error_message"],
            created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            updated_at=datetime.fromisoformat(r["updated_at"]) if r["updated_at"] else None,
        )

    # ================= Ayarlar =================

    def get_settings(self) -> ClipPipelineSettings:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'pipeline_settings'")
            row = cursor.fetchone()
            if row:
                try:
                    data = json.loads(row["value"])
                    return ClipPipelineSettings(**data)
                except Exception as e:
                    logger.warning(f"Failed to parse settings JSON: {e}")
            return ClipPipelineSettings()

    def save_settings(self, settings: ClipPipelineSettings):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO settings (key, value) VALUES ('pipeline_settings', ?)
            """, (settings.model_dump_json(),))
            conn.commit()


# Singleton ornek
clip_db = ClipDatabase()
