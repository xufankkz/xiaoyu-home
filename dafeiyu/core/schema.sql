-- 大肥鱼 v2 · 唯一事实源（SQLite，WAL）
-- 所有表带显式字段；禁止把结构化数据塞进纯文本。

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=OFF;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS memory_item (
  id            TEXT PRIMARY KEY,
  layer         TEXT NOT NULL,
  subtype       TEXT NOT NULL DEFAULT '',
  content       TEXT NOT NULL DEFAULT '',
  summary       TEXT NOT NULL DEFAULT '',
  skeleton      TEXT NOT NULL DEFAULT '',
  concepts      TEXT NOT NULL DEFAULT '[]',
  emotion_tag   TEXT NOT NULL DEFAULT '平静',
  salience      REAL NOT NULL DEFAULT 0.5,
  strength      REAL NOT NULL DEFAULT 1.0,
  level         INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'open',
  refs          TEXT NOT NULL DEFAULT '[]',
  t_created     TEXT NOT NULL,
  last_access   TEXT NOT NULL DEFAULT '',
  access_count  INTEGER NOT NULL DEFAULT 0,
  tombstone     INTEGER NOT NULL DEFAULT 0,
  origin        TEXT NOT NULL DEFAULT '',
  cluster       TEXT NOT NULL DEFAULT '',
  vision_tiles  TEXT DEFAULT NULL,
  animation_meta TEXT DEFAULT NULL,
  ext           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_mi_layer   ON memory_item(layer, level, tombstone);
CREATE INDEX IF NOT EXISTS idx_mi_status  ON memory_item(status, layer);
CREATE INDEX IF NOT EXISTS idx_mi_created ON memory_item(t_created);
CREATE INDEX IF NOT EXISTS idx_mi_tag     ON memory_item(emotion_tag);
CREATE INDEX IF NOT EXISTS idx_mi_cluster ON memory_item(cluster);

CREATE TABLE IF NOT EXISTS thought_node (
  id            TEXT PRIMARY KEY,
  chain_id      TEXT NOT NULL,
  type          TEXT NOT NULL,
  content       TEXT NOT NULL DEFAULT '',
  summary       TEXT NOT NULL DEFAULT '',
  skeleton      TEXT NOT NULL DEFAULT '',
  concepts      TEXT NOT NULL DEFAULT '[]',
  task_ref      TEXT NOT NULL DEFAULT '',
  salience      REAL NOT NULL DEFAULT 0.5,
  confidence    REAL NOT NULL DEFAULT 0.5,
  status        TEXT NOT NULL DEFAULT 'open',
  refs          TEXT NOT NULL DEFAULT '[]',
  strength      REAL NOT NULL DEFAULT 1.0,
  level         INTEGER NOT NULL DEFAULT 0,
  t_created     TEXT NOT NULL,
  last_access   TEXT NOT NULL DEFAULT '',
  access_count  INTEGER NOT NULL DEFAULT 0,
  tombstone     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tn_chain ON thought_node(chain_id, t_created);
CREATE INDEX IF NOT EXISTS idx_tn_open  ON thought_node(status, tombstone);
CREATE INDEX IF NOT EXISTS idx_tn_type  ON thought_node(type);

CREATE TABLE IF NOT EXISTS graph_edge (
  src  TEXT NOT NULL,
  dst  TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'ref',
  PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_ge_dst ON graph_edge(dst);

CREATE TABLE IF NOT EXISTS inverted_index (
  token      TEXT NOT NULL,
  owner      TEXT NOT NULL,
  owner_kind TEXT NOT NULL,
  tf         INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (token, owner, owner_kind)
);

CREATE TABLE IF NOT EXISTS embedding (
  owner      TEXT NOT NULL,
  owner_kind TEXT NOT NULL,
  backend    TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vec        BLOB NOT NULL,
  t          TEXT NOT NULL,
  PRIMARY KEY (owner, owner_kind, backend)
);

CREATE TABLE IF NOT EXISTS learning_intent (
  id                    TEXT PRIMARY KEY,
  target                TEXT NOT NULL,
  gap_type              TEXT NOT NULL,
  curiosity_score       REAL NOT NULL DEFAULT 0,
  expected_learnability REAL NOT NULL DEFAULT 0,
  source                TEXT NOT NULL DEFAULT 'dialog',
  context               TEXT NOT NULL DEFAULT '{}',
  proposed_query        TEXT NOT NULL DEFAULT '',
  status                TEXT NOT NULL DEFAULT 'queued',
  result_ref            TEXT NOT NULL DEFAULT '',
  attempts              INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT NOT NULL DEFAULT '',
  t_created             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_li_status ON learning_intent(status, curiosity_score DESC);

CREATE TABLE IF NOT EXISTS knowledge_item (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  domain       TEXT NOT NULL DEFAULT '',
  concepts     TEXT NOT NULL DEFAULT '[]',
  content      TEXT NOT NULL DEFAULT '',
  slots        TEXT NOT NULL DEFAULT '{}',
  filled_ratio REAL NOT NULL DEFAULT 0,
  uncertainty  REAL NOT NULL DEFAULT 0.5,
  confidence   REAL NOT NULL DEFAULT 0.5,
  source       TEXT NOT NULL DEFAULT '',
  intent_id    TEXT NOT NULL DEFAULT '',
  learned_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ki_name   ON knowledge_item(name);
CREATE INDEX IF NOT EXISTS idx_ki_domain ON knowledge_item(domain);

CREATE TABLE IF NOT EXISTS evidence (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  target      TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '',
  uncertainty REAL NOT NULL DEFAULT 0.5,
  t           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_target ON evidence(target);

CREATE TABLE IF NOT EXISTS emotion_snapshot (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  t           TEXT NOT NULL,
  joy         REAL, happiness REAL, sadness REAL, inferiority REAL, shyness REAL,
  dominant    TEXT NOT NULL DEFAULT '',
  reason      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_es_t ON emotion_snapshot(t);

CREATE TABLE IF NOT EXISTS reward_event (
  id          TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  trigger     TEXT NOT NULL,
  source_text TEXT NOT NULL DEFAULT '',
  delta       TEXT NOT NULL DEFAULT '{}',
  rpe_weight  REAL NOT NULL DEFAULT 1.0,
  t           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_re_t ON reward_event(t);
CREATE INDEX IF NOT EXISTS idx_re_trigger ON reward_event(trigger, t);

CREATE TABLE IF NOT EXISTS chitchat_log (
  id        TEXT PRIMARY KEY,
  t         TEXT NOT NULL,
  content   TEXT NOT NULL DEFAULT '',
  replied   INTEGER NOT NULL DEFAULT 0,
  replied_at TEXT NOT NULL DEFAULT '',
  emotion   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cl_t ON chitchat_log(t);

CREATE TABLE IF NOT EXISTS dialogue_message (
  id          TEXT PRIMARY KEY,
  t           TEXT NOT NULL,
  role        TEXT NOT NULL,
  text        TEXT NOT NULL,
  conv        TEXT NOT NULL DEFAULT '',
  emotion     TEXT NOT NULL DEFAULT '',
  memory_hits TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_dm_t ON dialogue_message(t);

CREATE TABLE IF NOT EXISTS audit_event (
  id          TEXT PRIMARY KEY,
  t           TEXT NOT NULL,
  entity_kind TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  action      TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ae_entity ON audit_event(entity_id, t);
CREATE INDEX IF NOT EXISTS idx_ae_t ON audit_event(t);

CREATE TABLE IF NOT EXISTS consolidation_run (
  id    TEXT PRIMARY KEY,
  t     TEXT NOT NULL,
  kind  TEXT NOT NULL,
  stats TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS schema_migration (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

-- ── v4 增量：跨会话思维链残留通道 ──────────────────────────────────────────
-- 只存索引，不复制正文；正文仍从 thought_node 按 node_id 读。
CREATE TABLE IF NOT EXISTS residual_index (
  node_id        TEXT PRIMARY KEY,
  chain_id       TEXT NOT NULL DEFAULT '',
  session_tag    TEXT NOT NULL DEFAULT '',
  last_active_at TEXT NOT NULL DEFAULT '',
  closed_at      TEXT NOT NULL DEFAULT '',
  in_residual    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ri_active ON residual_index(in_residual, last_active_at);

-- ── v5 增量：费用归因（每次模型调用一条）────────────────────────────────────
CREATE TABLE IF NOT EXISTS cost_event (
  id                 TEXT PRIMARY KEY,
  t                  TEXT NOT NULL,
  feature_tag        TEXT NOT NULL DEFAULT 'other',
  source             TEXT NOT NULL DEFAULT '',
  model              TEXT NOT NULL DEFAULT '',
  task               TEXT NOT NULL DEFAULT '',
  input_tokens       INTEGER NOT NULL DEFAULT 0,
  output_tokens      INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  estimated_cost     REAL NOT NULL DEFAULT 0,
  actual_cost        REAL NOT NULL DEFAULT 0,
  currency           TEXT NOT NULL DEFAULT 'CNY',
  session_id         TEXT NOT NULL DEFAULT '',
  message_id         TEXT NOT NULL DEFAULT '',
  meta               TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ce_t   ON cost_event(t);
CREATE INDEX IF NOT EXISTS idx_ce_tag ON cost_event(feature_tag, t);

-- 动态表情字典：同一张（sha256）不重复识图
CREATE TABLE IF NOT EXISTS sticker_cache (
  sha256      TEXT PRIMARY KEY,
  t           TEXT NOT NULL,
  frames      INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  summary     TEXT NOT NULL DEFAULT '',
  concepts    TEXT NOT NULL DEFAULT '[]',
  note        TEXT NOT NULL DEFAULT '',
  hits        INTEGER NOT NULL DEFAULT 1,
  last_seen   TEXT NOT NULL DEFAULT ''
);

-- ── v3 增量：视觉资产 ──────────────────────────────────────────────────────
-- 一张图（含切块方案与识别摘要）一条；memory_item.vision_tiles 里存指针与切块清单。
CREATE TABLE IF NOT EXISTS vision_asset (
  id             TEXT PRIMARY KEY,
  t              TEXT NOT NULL,
  source         TEXT NOT NULL DEFAULT '',
  kind           TEXT NOT NULL DEFAULT 'upload',
  width          INTEGER NOT NULL DEFAULT 0,
  height         INTEGER NOT NULL DEFAULT 0,
  tiled          INTEGER NOT NULL DEFAULT 0,
  tiles          INTEGER NOT NULL DEFAULT 0,
  manifest       TEXT NOT NULL DEFAULT '{}',
  summary        TEXT NOT NULL DEFAULT '',
  memory_item_id TEXT NOT NULL DEFAULT '',
  created_from   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_va_t   ON vision_asset(t);
CREATE INDEX IF NOT EXISTS idx_va_mem ON vision_asset(memory_item_id);

-- ── v3 增量：拒绝决策 ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS refusal_decision (
  id             TEXT PRIMARY KEY,
  t              TEXT NOT NULL,
  request_summary TEXT NOT NULL DEFAULT '',
  signals        TEXT NOT NULL DEFAULT '{}',
  propensity     REAL NOT NULL DEFAULT 0,
  threshold      REAL NOT NULL DEFAULT 0,
  decision       TEXT NOT NULL DEFAULT 'ACCEPT',
  reason_text    TEXT NOT NULL DEFAULT '',
  style          TEXT NOT NULL DEFAULT 'normal',
  alternatives   TEXT NOT NULL DEFAULT '[]',
  user_reaction  TEXT NOT NULL DEFAULT '',
  reacted_at     TEXT NOT NULL DEFAULT '',
  linked_thought_node_id TEXT NOT NULL DEFAULT '',
  linked_intent_id       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rd_t        ON refusal_decision(t);
CREATE INDEX IF NOT EXISTS idx_rd_decision ON refusal_decision(decision, t);

-- ── v12 增量：未完成话题 + 灵魂锚点报警 ────────────────────────────────────
-- 会话断开时标记"还没聊完的话题"，冷启动优先注入；72 小时没被提起就降级。
CREATE TABLE IF NOT EXISTS pending_topic (
  id            TEXT PRIMARY KEY,
  session_tag   TEXT NOT NULL DEFAULT '',
  conv          TEXT NOT NULL DEFAULT '',
  topic         TEXT NOT NULL DEFAULT '',
  node_ids      TEXT NOT NULL DEFAULT '[]',
  opened_at     TEXT NOT NULL DEFAULT '',
  last_seen     TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'open',
  downgraded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pt_status ON pending_topic(status, last_seen);

CREATE TABLE IF NOT EXISTS ethos_audit (
  id       TEXT PRIMARY KEY,
  t        TEXT NOT NULL,
  kind     TEXT NOT NULL DEFAULT '',
  expected TEXT NOT NULL DEFAULT '',
  actual   TEXT NOT NULL DEFAULT '',
  meta     TEXT NOT NULL DEFAULT '{}'
);

-- ── v14 增量：瞬时记忆冷存储（18 小时内可被"回忆"检索，常规对话不进上下文）──
CREATE TABLE IF NOT EXISTS instant_memory (
  id         TEXT PRIMARY KEY,
  ts         TEXT NOT NULL,
  role       TEXT NOT NULL DEFAULT 'user',
  content    TEXT NOT NULL DEFAULT '',
  concepts   TEXT NOT NULL DEFAULT '[]',
  conv       TEXT NOT NULL DEFAULT '',
  is_owner   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_im_ts ON instant_memory(ts);

-- Daily whitelist impressions: additive tables, independent from live emotion.
CREATE TABLE IF NOT EXISTS affinity_material (
  event_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, day TEXT NOT NULL, at TEXT NOT NULL,
  contact TEXT NOT NULL, conversation TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  feeling TEXT NOT NULL DEFAULT '{}', deleted INTEGER NOT NULL DEFAULT 0,
  reviewed_on TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_affinity_material_day ON affinity_material(day,contact);
CREATE TABLE IF NOT EXISTS affinity_daily_run (
  day TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS affinity_contact_day (
  day TEXT NOT NULL, contact TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(day,contact)
);
CREATE TRIGGER IF NOT EXISTS affinity_source_deleted AFTER DELETE ON memory_item BEGIN
  UPDATE affinity_material SET deleted=1,content='',feeling='{}' WHERE memory_id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS affinity_source_tombstone AFTER UPDATE OF tombstone ON memory_item
WHEN new.tombstone=1 AND COALESCE(json_extract(new.ext,'$.distilled'),0)=0 BEGIN
  UPDATE affinity_material SET deleted=1,content='',feeling='{}' WHERE memory_id=new.id;
END;

-- Additive v39 hybrid retrieval index. Raw records remain authoritative.
CREATE TABLE IF NOT EXISTS search_dirty(owner TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(owner,kind));
CREATE TABLE IF NOT EXISTS search_document(owner TEXT NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL,fingerprint TEXT NOT NULL,PRIMARY KEY(owner,kind));
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(owner UNINDEXED,body,labels,tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(owner UNINDEXED,body,labels,tokenize='unicode61');
DROP TRIGGER IF EXISTS hybrid_memory_insert;
CREATE TRIGGER hybrid_memory_insert AFTER INSERT ON memory_item BEGIN INSERT INTO search_dirty(owner,kind) VALUES(new.id,'memory') ON CONFLICT(owner,kind) DO NOTHING; END;
DROP TRIGGER IF EXISTS hybrid_memory_update;
CREATE TRIGGER hybrid_memory_update AFTER UPDATE OF content,summary,skeleton,concepts,layer,tombstone,level ON memory_item BEGIN INSERT INTO search_dirty(owner,kind) VALUES(new.id,'memory') ON CONFLICT(owner,kind) DO NOTHING; END;
DROP TRIGGER IF EXISTS hybrid_memory_delete;
CREATE TRIGGER hybrid_memory_delete AFTER DELETE ON memory_item BEGIN INSERT INTO search_dirty(owner,kind) VALUES(old.id,'memory') ON CONFLICT(owner,kind) DO NOTHING; END;
DROP TRIGGER IF EXISTS hybrid_knowledge_insert;
CREATE TRIGGER hybrid_knowledge_insert AFTER INSERT ON knowledge_item BEGIN INSERT INTO search_dirty(owner,kind) VALUES(new.id,'knowledge') ON CONFLICT(owner,kind) DO NOTHING; END;
DROP TRIGGER IF EXISTS hybrid_knowledge_update;
CREATE TRIGGER hybrid_knowledge_update AFTER UPDATE OF name,content,concepts ON knowledge_item BEGIN INSERT INTO search_dirty(owner,kind) VALUES(new.id,'knowledge') ON CONFLICT(owner,kind) DO NOTHING; END;
DROP TRIGGER IF EXISTS hybrid_knowledge_delete;
CREATE TRIGGER hybrid_knowledge_delete AFTER DELETE ON knowledge_item BEGIN INSERT INTO search_dirty(owner,kind) VALUES(old.id,'knowledge') ON CONFLICT(owner,kind) DO NOTHING; END;
