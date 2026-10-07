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
          action_id TEXT REFERENCES improvement_items(id) ON DELETE CASCADE,
          source TEXT NOT NULL DEFAULT 'diagnosis', brief TEXT NOT NULL DEFAULT '',
          channel TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
          objective TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '专业、克制、具体',
          keywords TEXT NOT NULL DEFAULT '', target_length INTEGER NOT NULL DEFAULT 1200,
          title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
          facts TEXT NOT NULL DEFAULT '', cover TEXT NOT NULL DEFAULT '',
          source_bundle_json TEXT NOT NULL DEFAULT '{}', generation_meta_json TEXT NOT NULL DEFAULT '{}',
          quality_report_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
          reviewed_revision INTEGER, reviewer TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(action_id));
        CREATE TABLE IF NOT EXISTS publishing_jobs (
          id TEXT PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
          asset_id TEXT NOT NULL REFERENCES editorial_assets(id) ON DELETE CASCADE,
          platform TEXT NOT NULL, asset_revision INTEGER NOT NULL,
          title_snapshot TEXT NOT NULL, body_snapshot TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'manual_required', receipt_url TEXT, operator TEXT,
          mode TEXT NOT NULL DEFAULT 'manual', adapter_note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(asset_id,platform,asset_revision));
        CREATE TABLE IF NOT EXISTS asset_sources (
          id TEXT PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
          asset_id TEXT NOT NULL REFERENCES editorial_assets(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
          file_name TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
          mime TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0,
          extracted_text TEXT NOT NULL DEFAULT '',
          extract_status TEXT NOT NULL DEFAULT 'pending', extract_note TEXT NOT NULL DEFAULT '',
          fetched_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS asset_sources_by_asset ON asset_sources(asset_id, created_at);
        CREATE TABLE IF NOT EXISTS workspace_preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_execution_per_project
          ON execution_runs(project_slug)
          WHERE status IN ('frozen','preflight','ready','running','paused','interrupted','blocked','login');
        ''')
        # Content may be produced without a diagnosis, so action_id must be optional.
        info = {row['name']: dict(row) for row in c.execute('PRAGMA table_info(editorial_assets)')}
        needs_rebuild = ('source' not in info) or bool(info.get('action_id', {}).get('notnull'))
        if needs_rebuild:
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('PRAGMA legacy_alter_table=ON')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS editorial_assets_rebuilt (
              id TEXT PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
              action_id TEXT REFERENCES improvement_items(id) ON DELETE CASCADE,
              source TEXT NOT NULL DEFAULT 'diagnosis', brief TEXT NOT NULL DEFAULT '',
              channel TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
              objective TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '专业、克制、具体',
              keywords TEXT NOT NULL DEFAULT '', target_length INTEGER NOT NULL DEFAULT 1200,
              title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
              facts TEXT NOT NULL DEFAULT '', cover TEXT NOT NULL DEFAULT '',
              source_bundle_json TEXT NOT NULL DEFAULT '{}', generation_meta_json TEXT NOT NULL DEFAULT '{}',
              quality_report_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
              reviewed_revision INTEGER, reviewer TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(action_id));
            INSERT OR IGNORE INTO editorial_assets_rebuilt
              (id,project_slug,action_id,source,brief,title,body,facts,status,revision,
               reviewed_revision,reviewer,created_at,updated_at)
              SELECT id,project_slug,action_id,'diagnosis','',title,body,facts,status,revision,
                     reviewed_revision,reviewer,created_at,updated_at
              FROM editorial_assets;
            DROP TABLE editorial_assets;
            ALTER TABLE editorial_assets_rebuilt RENAME TO editorial_assets;
            ''')
            c.execute('PRAGMA legacy_alter_table=OFF')
            c.execute('PRAGMA foreign_keys=ON')
        # Evidence-driven content fields are additive so existing assets and reviews survive migration.
        asset_cols = {row['name'] for row in c.execute('PRAGMA table_info(editorial_assets)')}
        content_fields = (
            ('channel', "TEXT NOT NULL DEFAULT ''"),
            ('audience', "TEXT NOT NULL DEFAULT ''"),
            ('objective', "TEXT NOT NULL DEFAULT ''"),
            ('tone', "TEXT NOT NULL DEFAULT '专业、克制、具体'"),
            ('keywords', "TEXT NOT NULL DEFAULT ''"),
            ('target_length', 'INTEGER NOT NULL DEFAULT 1200'),
            ('summary', "TEXT NOT NULL DEFAULT ''"),
            ('cover', "TEXT NOT NULL DEFAULT ''"),
            ('source_bundle_json', "TEXT NOT NULL DEFAULT '{}'"),
            ('generation_meta_json', "TEXT NOT NULL DEFAULT '{}'"),
            ('quality_report_json', "TEXT NOT NULL DEFAULT '{}'"),
        )
        for name, ddl in content_fields:
            if name not in asset_cols:
                c.execute(f'ALTER TABLE editorial_assets ADD COLUMN {name} {ddl}')
        # Publishing jobs record which adapter handled them and why they still need a human.
        job_cols = {row['name'] for row in c.execute('PRAGMA table_info(publishing_jobs)')}
        for name, ddl in (('mode', "TEXT NOT NULL DEFAULT 'manual'"),
                          ('adapter_note', "TEXT NOT NULL DEFAULT ''")):
            if name not in job_cols:
                c.execute(f'ALTER TABLE publishing_jobs ADD COLUMN {name} {ddl}')
        # 修数据：诊断优化清单的内部话术曾被原样复制进 objective（该字段会作为 brief.objective
        # 送进内容模型）。这里只修「症状可识别」的旧行，并以 source_bundle 里当时的提问重算；
        # 重算不出来就清空（质量门禁会提示「未填写写作目标」，比留着内部话术安全）。
        # 幂等：修完不再有行匹配该模式，重跑 migrate() 不会重复动数据。
        leaked = c.execute("""SELECT id, source_bundle_json FROM editorial_assets
            WHERE objective LIKE '诊断问题%个有效观测中未自然提及主体%'""").fetchall()
        for row in leaked:
            try:
                question = json.loads(row['source_bundle_json'] or '{}').get('question')
            except (TypeError, ValueError):
                question = ''
            c.execute('UPDATE editorial_assets SET objective=? WHERE id=?',
                      (reader_objective(question), row['id']))


def reader_objective(question=''):
    """内容稿件的 objective 只能是「给读者的写作目标」。

    真实事故：editorial_flow.create_asset 把 improvement_items.description——「诊断问题 Q05 在
    2 个有效观测中未自然提及主体。请基于企业可核验资料补充内容。涉及平台：deepseek、metaso。」
    ——原样写进 objective。而 objective 会作为 brief.objective 送进内容模型，等于把内部过程信息
    （诊断项、观测数、平台名）塞进了稿件字段。内部说明仍留在优化清单的 description 里，
    source_bundle 也带 observations，这里丢掉不丢信息。

    问题为空时宁可为空（质量门禁会提示「未填写写作目标」），也不替编辑臆造写作目标。
    放在 store 层是因为建稿（editorial_flow）和修数据（migrate）两处都要用同一套措辞，
    分开写两份迟早会漂。
    """
    text = str(question or '').strip().rstrip('？?。')
    return f'回答读者提出的问题：{text}' if text else ''


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
