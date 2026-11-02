# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""视觉资产落库：vision_asset 表 + memory_item.vision_tiles 附件列。"""

from __future__ import annotations

import json
from datetime import datetime

from .. import db
from ..entity import new_id, now_iso
from ..repo import memory as mr
from ..repo import system as sr


def attach(plan, report: dict, *, memory_item_id: str = "", kind: str = "upload",
           source: str = "", created_from: str = "", concepts: list[str] | None = None,
           animation_meta: dict | None = None) -> dict:
    """把一次视觉处理写成一条资产，并把指针挂到记忆条目上。"""
    asset_id = new_id()
    summary = (report or {}).get("summary", "")[:4000]
    raw_manifest = getattr(plan, "manifest", {})
    manifest = dict(raw_manifest() if callable(raw_manifest) else (raw_manifest or {}))
    manifest["summary"] = summary
    tiled = bool(getattr(plan, "tiled", False))
    tile_count = int(getattr(plan, "count", 0) or 0)
    width = int(getattr(plan, "width", 0) or 0)
    height = int(getattr(plan, "height", 0) or 0)
    db.ex(
        "INSERT INTO vision_asset(id, t, source, kind, width, height, tiled, tiles, manifest, "
        "summary, memory_item_id, created_from) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, now_iso(), source or getattr(plan, "image_path", "") or getattr(plan, "source", ""),
         kind, width, height, 1 if tiled else 0, tile_count,
         json.dumps(manifest, ensure_ascii=False), summary, memory_item_id, created_from),
    )

    if memory_item_id:
        item = mr.get(memory_item_id)
        if item is not None:
            pointer = {
                "asset_id": asset_id, "tiled": tiled,
                "tile": int(getattr(plan, "tile_size", 0) or 0),
                "overlap": int(getattr(plan, "overlap", 0) or 0),
                "count": tile_count,
                "thumb": getattr(plan, "thumb", ""),
                "tiles": manifest.get("tiles", []), "summary": summary[:400],
            }
            # 只补不删：概念合并，旧概念一个不动（不重写历史记忆）
            merged = list(dict.fromkeys(list(item.concepts or []) + list(concepts or [])))[:16]
            fields = {"vision_tiles": json.dumps(pointer, ensure_ascii=False), "concepts": merged}
            if animation_meta:
                fields["animation_meta"] = json.dumps(animation_meta, ensure_ascii=False)
            mr.update_fields(memory_item_id, **fields)
            mr.set_index(memory_item_id, "memory", [], merged)

    sr.audit_add("vision", asset_id, "created",
                 {"kind": kind, "source": (source or str(getattr(plan, "image_path", "")))[:200],
                  "tiles": tile_count, "tiled": tiled,
                  "coverage": (report or {}).get("coverage"), "memory_item_id": memory_item_id,
                  "animation": bool(animation_meta)})
    return {"asset_id": asset_id, "tiles": tile_count, "tiled": tiled, "summary": summary}


def get(asset_id: str) -> dict | None:
    row = db.q1("SELECT * FROM vision_asset WHERE id=?", (asset_id,))
    if row is None:
        return None
    data = dict(row)
    try:
        data["manifest"] = json.loads(data.get("manifest") or "{}")
    except Exception:
        data["manifest"] = {}
    return data


def recent(limit: int = 20) -> list[dict]:
    rows = db.q("SELECT id, t, source, kind, width, height, tiled, tiles, summary, memory_item_id "
                "FROM vision_asset ORDER BY t DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in rows]


def count() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM vision_asset", (), 0) or 0)
