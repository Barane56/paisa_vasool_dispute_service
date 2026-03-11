"""
SQL Migration Runner
====================
Reads .sql files from sql/versions/ in lexicographic order, checks
schema_migrations to skip already-applied ones, and runs each new file
inside its own transaction so a failure rolls back only that migration.

Usage (CLI):
    python -m src.data.migrations.runner upgrade        # apply all pending
    python -m src.data.migrations.runner status         # show applied / pending
    python -m src.data.migrations.runner applied        # list applied versions

Usage (programmatic):
    from src.data.migrations.runner import run_upgrade
    await run_upgrade()
"""
import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

from src.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

_MIGRATIONS_DIR = Path(__file__).parent / "v1"
_VERSIONS_DIR   = _MIGRATIONS_DIR / "versions"
_BOOTSTRAP_SQL  = _MIGRATIONS_DIR / "0000_bootstrap.sql"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_sql_files() -> list[Path]:
    """Return all version SQL files sorted lexicographically."""
    return sorted(_VERSIONS_DIR.glob("*.sql"))


def _version_from_path(path: Path) -> str:
    """Extract version key from filename e.g. '0001_initial_schema' from '0001_initial_schema.sql'."""
    return path.stem


def _description_from_path(path: Path) -> str:
    """Extract human-readable description from filename."""
    return path.stem.replace("_", " ").title()


async def _get_connection() -> asyncpg.Connection:
    """Open a raw asyncpg connection (bypasses SQLAlchemy for direct SQL execution)."""
    # asyncpg uses postgresql:// not postgresql+asyncpg://
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(url)


async def _bootstrap(conn: asyncpg.Connection) -> None:
    """Ensure schema_migrations table exists."""
    sql = _BOOTSTRAP_SQL.read_text(encoding="utf-8")
    await conn.execute(sql)
    logger.debug("Bootstrap complete — schema_migrations table is ready")


async def _get_applied_versions(conn: asyncpg.Connection) -> set[str]:
    """Return set of already-applied version strings."""
    rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    return {row["version"] for row in rows}


async def _apply_migration(conn: asyncpg.Connection, path: Path) -> None:
    """Apply a single SQL file inside a transaction. Rolls back on any error."""
    version     = _version_from_path(path)
    description = _description_from_path(path)
    sql         = path.read_text(encoding="utf-8")

    logger.info("Applying migration: %s", version)

    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO schema_migrations (version, description) VALUES ($1, $2)",
            version,
            description,
        )

    logger.info("Migration applied:  %s ✓", version)


# ── Public API ─────────────────────────────────────────────────────────────────

async def run_upgrade() -> None:
    """Apply all pending migrations in order."""
    conn = await _get_connection()
    try:
        await _bootstrap(conn)
        applied = await _get_applied_versions(conn)
        pending = [f for f in _get_sql_files() if _version_from_path(f) not in applied]

        if not pending:
            logger.info("Database is up to date — no pending migrations")
            return

        logger.info("Found %d pending migration(s)", len(pending))
        for path in pending:
            await _apply_migration(conn, path)

        logger.info("All migrations applied successfully")
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        raise
    finally:
        await conn.close()


async def run_status() -> None:
    """Print applied and pending migrations."""
    conn = await _get_connection()
    try:
        await _bootstrap(conn)
        applied = await _get_applied_versions(conn)
        all_files = _get_sql_files()

        print("\n── Migration Status ──────────────────────────────")
        if not all_files:
            print("  No migration files found.")
        for path in all_files:
            version = _version_from_path(path)
            mark    = "✓ applied" if version in applied else "✗ pending"
            print(f"  [{mark}]  {version}")
        print()

        # Show versions in DB that have no corresponding file (manual entries etc.)
        file_versions = {_version_from_path(f) for f in all_files}
        orphaned = applied - file_versions
        if orphaned:
            print("── Orphaned (in DB but no file) ──────────────────")
            for v in sorted(orphaned):
                print(f"  [? orphan]  {v}")
            print()
    finally:
        await conn.close()


async def run_applied() -> None:
    """List all applied migrations from the DB."""
    conn = await _get_connection()
    try:
        await _bootstrap(conn)
        rows = await conn.fetch(
            "SELECT version, description, applied_at FROM schema_migrations ORDER BY version"
        )
        print("\n── Applied Migrations ────────────────────────────")
        if not rows:
            print("  None yet.")
        for row in rows:
            print(f"  {row['version']}  |  {row['applied_at'].strftime('%Y-%m-%d %H:%M:%S')}  |  {row['description']}")
        print()
    finally:
        await conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

def _usage() -> None:
    print(
        "\nUsage: python -m src.data.migrations.runner <command>\n\n"
        "Commands:\n"
        "  upgrade    Apply all pending SQL migrations\n"
        "  status     Show applied and pending migrations\n"
        "  applied    List all applied migrations from DB\n"
    )


async def _main(args: list[str]) -> None:
    if not args:
        _usage()
        sys.exit(1)

    command = args[0]

    if command == "upgrade":
        await run_upgrade()
    elif command == "status":
        await run_status()
    elif command == "applied":
        await run_applied()
    else:
        print(f"Unknown command: {command}")
        _usage()
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(_main(sys.argv[1:]))