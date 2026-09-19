"""Independent workspace storage. Never opens the legacy application's files."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('GEO_REDESIGN_DATA', str(ROOT / 'data'))).resolve()
DB = DATA / 'redesign.db'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def migrate():
    """Additive migration: retain all pre-existing user tables and rows."""
    with connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
          slug TEXT PRIMARY KEY, name TEXT NOT NULL,
          target_type TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_profiles (
          project_slug TEXT PRIMARY KEY REFERENCES projects(slug) ON DELETE CASCADE,
          profile_json TEXT NOT NULL DEFAULT '{}',
          questions_json TEXT NOT NULL DEFAULT '[]',
          platforms_json TEXT NOT NULL DEFAULT '[]',
          revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workflow_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_slug TEXT REFERENCES projects(slug) ON DELETE CASCADE,
          kind TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS execution_runs (
          id TEXT PRIMARY KEY,
          project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
          status TEXT NOT NULL, config_json TEXT NOT NULL, config_hash TEXT NOT NULL,
          profile_revision INTEGER NOT NULL, run_path TEXT,
          blocker TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS improvement_items (
          id TEXT PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
          run_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
          task_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'todo', created_at TEXT NOT NULL,
          UNIQUE(run_id,task_id));
        CREATE TABLE IF NOT EXISTS editorial_assets (
          id TEXT PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
          action_id TEXT NOT NULL REFERENCES improvement_items(id) ON DELETE CASCADE,
          title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', facts TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
          reviewed_revision INTEGER, reviewer TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(action_id));
        CREATE TABLE IF NOT EXISTS publishing_jobs (
          id TEXT PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
          asset_id TEXT NOT NULL REFERENCES editorial_assets(id) ON DELETE CASCADE,
          platform TEXT NOT NULL, asset_revision INTEGER NOT NULL,
          title_snapshot TEXT NOT NULL, body_snapshot TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'manual_required', receipt_url TEXT, operator TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(asset_id,platform,asset_revision));
        CREATE TABLE IF NOT EXISTS workspace_preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_execution_per_project
          ON execution_runs(project_slug)
          WHERE status IN ('frozen','preflight','ready','running','paused','interrupted','blocked','login');
        ''')


def record_event(c, slug, kind, title):
    c.execute('INSERT INTO workflow_events(project_slug,kind,title,created_at) VALUES(?,?,?,?)',
              (slug, kind, title, now()))


def get_profile(slug):
    with connection() as c:
        if not c.execute('SELECT 1 FROM projects WHERE slug=?', (slug,)).fetchone():
            raise KeyError('项目不存在')
        row = c.execute('SELECT * FROM project_profiles WHERE project_slug=?', (slug,)).fetchone()
        if not row:
            return dict(profile={}, questions=[], platforms=[], revision=0)
        return dict(profile=json.loads(row['profile_json']), questions=json.loads(row['questions_json']),
                    platforms=json.loads(row['platforms_json']), revision=row['revision'])


def save_profile(slug, profile, questions, platforms, revision):
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM projects WHERE slug=?', (slug,)).fetchone():
            raise KeyError('项目不存在')
        old = c.execute('SELECT revision FROM project_profiles WHERE project_slug=?', (slug,)).fetchone()
        current = old['revision'] if old else 0
        if revision != current:
            raise ValueError('资料已在其他页面更新，请刷新后再保存')
        c.execute('''INSERT INTO project_profiles VALUES(?,?,?,?,?,?)
          ON CONFLICT(project_slug) DO UPDATE SET profile_json=excluded.profile_json,
          questions_json=excluded.questions_json, platforms_json=excluded.platforms_json,
          revision=excluded.revision, updated_at=excluded.updated_at''',
          (slug, json.dumps(profile, ensure_ascii=False), json.dumps(questions, ensure_ascii=False),
           json.dumps(platforms, ensure_ascii=False), current + 1, now()))
        record_event(c, slug, 'profile_saved', '更新项目资料与诊断配置')
    return current + 1
