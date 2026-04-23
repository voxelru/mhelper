-- SQLite migration for mhelper (manual apply)
-- Includes schema changes introduced recently:
-- - tasks.description (TEXT, nullable)
-- - comments.resolved (INTEGER as boolean, NOT NULL default 0)
--
-- Apply (example):
--   sqlite3 instance/mhelper.sqlite3 ".read migration_2026-04-23_add_description_resolved.sql"
--
-- Notes:
-- - SQLite doesn't support IF NOT EXISTS for ADD COLUMN.
-- - Run this only once on a DB that doesn't yet have these columns.

BEGIN;

ALTER TABLE tasks ADD COLUMN description TEXT;

ALTER TABLE comments ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0;
UPDATE comments SET resolved = 0 WHERE resolved IS NULL;

COMMIT;

