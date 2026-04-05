"""Database client for SQLite operations"""

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from .schema import Base

logger = logging.getLogger("ott-hooks")


class DatabaseClient:
    """SQLite database client with connection pooling and health checks"""
    
    def __init__(
        self,
        db_path: str = "/config/ott-hooks.db",
        enable_wal: bool = True,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: int = 30,
    ):
        """Initialize database client
        
        Args:
            db_path: Path to SQLite database file
            enable_wal: Enable Write-Ahead Logging for better concurrency
            pool_size: Number of connections to keep in pool
            max_overflow: Maximum number of connections to create beyond pool_size
            pool_timeout: Timeout in seconds for getting a connection
        """
        self.db_path = Path(db_path)
        self.enable_wal = enable_wal
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._closing = False
        # Count of sessions currently checked out; used by close() to wait
        # for in-flight work to complete before disposing the engine.
        self._active_sessions = 0
        self._active_sessions_lock = threading.Lock()
        
        # Create database directory if it doesn't exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"[DB] Database path: {self.db_path}")
        logger.info(f"[DB] WAL mode: {enable_wal}")
    
    def initialize(self) -> None:
        """Initialize database connection and create tables"""
        with self._lock:
            if self._initialized:
                logger.warning("[DB] Already initialized, skipping")
                return
            
            try:
                # Create engine with connection pooling
                # Use StaticPool for SQLite to avoid threading issues
                connect_args = {"check_same_thread": False}
                
                self._engine = create_engine(
                    f"sqlite:///{self.db_path}",
                    connect_args=connect_args,
                    poolclass=StaticPool,
                    echo=False,  # Set to True for SQL debugging
                )
                
                # Enable WAL mode and other optimizations
                @event.listens_for(self._engine, "connect")
                def set_sqlite_pragma(dbapi_conn, connection_record):
                    cursor = dbapi_conn.cursor()
                    if self.enable_wal:
                        cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA cache_size=10000")
                    cursor.execute("PRAGMA temp_store=MEMORY")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()
                
                # Create session factory
                self._session_factory = sessionmaker(
                    bind=self._engine,
                    autocommit=False,
                    autoflush=False,
                )
                
                # Create all tables
                Base.metadata.create_all(self._engine)
                
                self._initialized = True
                logger.info("[DB] ✓ Database initialized successfully")
                
                # Log database info
                with self.session() as session:
                    result = session.execute(text("SELECT sqlite_version()"))
                    version = result.scalar()
                    logger.info(f"[DB] SQLite version: {version}")
                    
                    # Check if WAL is enabled
                    result = session.execute(text("PRAGMA journal_mode"))
                    journal_mode = result.scalar()
                    logger.info(f"[DB] Journal mode: {journal_mode}")
                
            except Exception as e:
                logger.error(f"[DB] Failed to initialize database: {e}", exc_info=True)
                raise
    
    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Context manager for database sessions

        Callers own the transaction: call session.commit() explicitly
        when writes complete. The context manager only handles rollback
        on exception and cleanup on exit, so read-only blocks cost nothing.

        Yields:
            SQLAlchemy session

        Example:
            with db.session() as session:
                session.add(obj)
                session.commit()

        Raises:
            RuntimeError: if close() is in progress - no new sessions.
        """
        if self._closing:
            raise RuntimeError("Database is shutting down; no new sessions")
        if not self._initialized:
            self.initialize()

        with self._active_sessions_lock:
            self._active_sessions += 1
        session = self._session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            with self._active_sessions_lock:
                self._active_sessions -= 1
    
    def health_check(self) -> dict[str, any]:
        """Perform database health check
        
        Returns:
            Dictionary with health check results
        """
        try:
            if not self._initialized:
                return {
                    "status": "unhealthy",
                    "error": "Database not initialized",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            
            with self.session() as session:
                # Test database connectivity
                session.execute(text("SELECT 1"))
                
                # Get database size
                db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
                db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
                
                # Get table counts
                from .schema import (
                    ProcessingMetricsModel,
                    JustWatchCacheModel,
                    OverrideHistoryModel,
                    TagHistoryModel,
                    WebhookLogModel,
                    ErrorLogModel,
                )
                
                metrics_count = session.query(ProcessingMetricsModel).count()
                cache_count = session.query(JustWatchCacheModel).count()
                override_count = session.query(OverrideHistoryModel).count()
                tag_count = session.query(TagHistoryModel).count()
                webhook_count = session.query(WebhookLogModel).count()
                error_count = session.query(ErrorLogModel).count()
                
                return {
                    "status": "healthy",
                    "timestamp": datetime.utcnow().isoformat(),
                    "database": {
                        "path": str(self.db_path),
                        "size_mb": db_size_mb,
                        "wal_enabled": self.enable_wal,
                    },
                    "tables": {
                        "processing_metrics": metrics_count,
                        "justwatch_cache": cache_count,
                        "override_history": override_count,
                        "tag_history": tag_count,
                        "webhook_log": webhook_count,
                        "error_log": error_count,
                    },
                }
                
        except Exception as e:
            logger.error(f"[DB] Health check failed: {e}", exc_info=True)
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }
    
    def cleanup_old_data(self, days: int = 90) -> dict[str, int]:
        """Clean up old data from database
        
        Args:
            days: Delete data older than this many days
            
        Returns:
            Dictionary with deletion counts per table
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            deleted_counts = {}
            
            with self.session() as session:
                from .schema import (
                    ProcessingMetricsModel,
                    WebhookLogModel,
                    ErrorLogModel,
                )
                
                # Clean up old metrics
                deleted = session.query(ProcessingMetricsModel).filter(
                    ProcessingMetricsModel.timestamp < cutoff_date
                ).delete()
                deleted_counts["processing_metrics"] = deleted
                
                # Clean up old webhook logs
                deleted = session.query(WebhookLogModel).filter(
                    WebhookLogModel.timestamp < cutoff_date
                ).delete()
                deleted_counts["webhook_log"] = deleted
                
                # Clean up resolved errors
                deleted = session.query(ErrorLogModel).filter(
                    ErrorLogModel.timestamp < cutoff_date,
                    ErrorLogModel.resolved == True
                ).delete()
                deleted_counts["error_log"] = deleted
                
                session.commit()
            
            logger.info(f"[DB] Cleanup complete: {deleted_counts}")
            return deleted_counts
            
        except Exception as e:
            logger.error(f"[DB] Cleanup failed: {e}", exc_info=True)
            return {}
    
    def vacuum(self) -> bool:
        """Run VACUUM to reclaim space and optimize database
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # VACUUM must run outside of a transaction
            with self._engine.connect() as conn:
                conn.execute(text("VACUUM"))
                conn.commit()
            
            logger.info("[DB] VACUUM completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"[DB] VACUUM failed: {e}", exc_info=True)
            return False
    
    def close(self, timeout: float = 30.0) -> None:
        """Close database connections gracefully.

        Flips _closing so new session() calls raise, polls for in-flight
        sessions to drain (bounded by timeout), then disposes the engine.
        Active sessions that exceed the timeout get their connections
        closed from under them, but we log loudly when that happens.

        Args:
            timeout: Seconds to wait for in-flight sessions before disposing.
        """
        import time

        with self._lock:
            if not self._engine:
                return
            self._closing = True

        deadline = time.monotonic() + timeout
        while True:
            with self._active_sessions_lock:
                active = self._active_sessions
            if active == 0:
                break
            if time.monotonic() >= deadline:
                logger.warning(
                    f"[DB] {active} active session(s) remain after {timeout}s "
                    "timeout; closing anyway"
                )
                break
            time.sleep(0.1)

        with self._lock:
            self._engine.dispose()
            self._initialized = False
            self._closing = False
            logger.info("[DB] Database connections closed")


# Global database instance (initialized in main.py)
db: Optional[DatabaseClient] = None


def get_db() -> DatabaseClient:
    """Get global database instance
    
    Returns:
        DatabaseClient instance
        
    Raises:
        RuntimeError: If database not initialized
    """
    if db is None:
        raise RuntimeError("Database not initialized. Call initialize_database() first.")
    return db


def initialize_database(db_path: str = "/config/ott-hooks.db", enable_wal: bool = True) -> DatabaseClient:
    """Initialize global database instance
    
    Args:
        db_path: Path to SQLite database file
        enable_wal: Enable Write-Ahead Logging
        
    Returns:
        DatabaseClient instance
    """
    global db
    db = DatabaseClient(db_path=db_path, enable_wal=enable_wal)
    db.initialize()
    return db
