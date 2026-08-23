"""Bridge dynamic routing topics back to legacy SYSTEM safety codes.

Routing uses the DB topic tree, but existing caption validation still consumes
``channel.niches``. When an operator selects only a dynamic descendant, mirror
its nearest SYSTEM ancestor(s) into that legacy field so niche-specific safety
rules remain active without flattening the new routing model.
"""
from __future__ import annotations

import json

from . import topic_engine

_INSTALLED = False


def _system_ancestor_codes(conn, topic_codes) -> list[str]:
    result = []
    seen = set()
    for code in topic_codes or []:
        row = conn.execute(
            "SELECT id,code,topic_type,parent_id FROM topic WHERE code=? AND status='ACTIVE'",
            (str(code),),
        ).fetchone()
        visited = set()
        while row and row["id"] not in visited:
            visited.add(row["id"])
            if row["topic_type"] == "SYSTEM":
                if row["code"] not in seen:
                    result.append(row["code"])
                    seen.add(row["code"])
                break
            parent_id = row["parent_id"]
            if not parent_id:
                break
            row = conn.execute(
                "SELECT id,code,topic_type,parent_id FROM topic WHERE id=? AND status='ACTIVE'",
                (parent_id,),
            ).fetchone()
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_set_rules = topic_engine.set_channel_rules

    def set_channel_rules(conn, channel_id: str, includes, excludes) -> dict:
        # Explicit exclusion is authoritative. The UI normally prevents a useful
        # reason to select both modes on one node, but repeated form values or a
        # stale browser state must still resolve deterministically to EXCLUDE.
        exclude_codes = []
        seen_excludes = set()
        for raw in excludes or []:
            code = str(raw or "").strip()
            if code and code not in seen_excludes:
                exclude_codes.append(code)
                seen_excludes.add(code)
        include_codes = []
        seen_includes = set()
        for raw in includes or []:
            code = str(raw or "").strip()
            if code and code not in seen_excludes and code not in seen_includes:
                include_codes.append(code)
                seen_includes.add(code)

        result = original_set_rules(conn, channel_id, include_codes, exclude_codes)
        safety_codes = _system_ancestor_codes(conn, result.get("includes") or [])
        conn.execute(
            "UPDATE channel SET niches=? WHERE id=?",
            (json.dumps(safety_codes, ensure_ascii=False), str(channel_id)),
        )
        return result

    topic_engine.set_channel_rules = set_channel_rules
    _INSTALLED = True
