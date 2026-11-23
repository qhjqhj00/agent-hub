#!/usr/bin/env python3
"""Antigravity (agy) v2 lifecycle hook:

- PreInvocation (0s non-blocking):
  1. Finds the local session file (`~/.agent-hub/sessions/<name>.json`).
  2. If `conversation` or `meta.t3_thread` is missing, deterministically resolves
     `t3_thread` via `resolve_t3_thread(conversationId)` and syncs both the
     local session file and `POST /v1/sessions/<id>/meta`.
  3. Reports `state = "busy"` (`POST /v1/sessions/<id>/state`).
  4. Non-blocking `GET /v1/sessions/<id>/pull?wait=0`: if items are waiting,
     injects them as a `systemMessage` step and calls `POST /v1/sessions/<id>/delivered`.

- Stop (0s non-blocking, NEVER sleeps/polls):
  1. Reports `state = "idle"` (`POST /v1/sessions/<id>/state`).
  2. Immediately returns `{}` so T3 UI returns to idle cleanly and
     `MAX_FORCED_INVOCATIONS` is never hit.

Usage in `<workspace>/.agents/hooks.json`:
  python3 -m agenthub.bridges.agy_hook [agent-name]
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from .common import (
    SessionAuthError,
    SessionHTTPClient,
    agenthub_home,
    list_session_files,
    read_session_file,
    resolve_t3_thread,
    update_session_file,
)


def _log(*args: Any) -> None:
    try:
        path = os.path.join(agenthub_home(), "agy-hook.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), " ".join(str(x) for x in args)))
    except OSError:
        pass


def _find_session(agent_arg: str, ev: dict[str, Any]) -> dict[str, Any] | None:
    if agent_arg:
        return read_session_file(agent_arg)
    cid = (ev.get("conversationId") or "").strip()
    cwds: list[str] = []
    if ev.get("cwd"):
        cwds.append(os.path.realpath(ev["cwd"]))
    for wp in ev.get("workspacePaths") or []:
        if isinstance(wp, str) and wp:
            rp = os.path.realpath(wp)
            if rp not in cwds:
                cwds.append(rp)
    candidates = list_session_files(kind="agy")
    if cid:
        for s in candidates:
            if s.get("conversation") == cid:
                return s
    for cwd in cwds:
        by_cwd = [s for s in candidates if s.get("cwd") and os.path.realpath(s["cwd"]) == cwd]
        if len(by_cwd) == 1:
            return by_cwd[0]
    return None


def _ensure_t3_binding(sess: dict[str, Any], http: SessionHTTPClient, ev: dict[str, Any]) -> dict[str, Any]:
    """Deterministically populate conversation and meta.t3_thread when missing."""
    cid = (ev.get("conversationId") or sess.get("conversation") or "").strip()
    if not cid:
        return sess
    meta = dict(sess.get("meta") or {})
    existing_tid = (meta.get("t3_thread") or "").strip()
    need_conv = sess.get("conversation") != cid
    new_tid = existing_tid or resolve_t3_thread(cid)
    need_tid = bool(new_tid and new_tid != existing_tid)

    if not need_conv and not need_tid:
        return sess

    meta_patch = {"t3_thread": new_tid} if need_tid else None
    try:
        http.meta(
            conversation=cid if need_conv else None,
            meta=meta_patch,
        )
    except Exception as e:
        _log("meta-sync-error", sess.get("name"), e)
    updated = update_session_file(
        sess["name"],
        conversation=cid if need_conv else None,
        meta=meta_patch,
    )
    if updated:
        _log("bound", sess.get("name"), "cid=%s" % cid, "t3_thread=%s" % (new_tid or "-"))
        return updated
    return sess


def _wrap(agent: str, items: list[dict[str, Any]]) -> str:
    body = "\n\n---\n\n".join(str(i.get("text") or i.get("content") or "") for i in items)
    return "[agent-hub] 以下是 hub 推送给你（%s）的消息，请按其中要求处理；回复用 hub_* 工具。\n\n%s" % (agent, body)


def handle_event(ev: dict[str, Any], agent_arg: str = "") -> dict[str, Any]:
    name = ev.get("hookEventName") or ""
    sess = _find_session(agent_arg, ev)
    if not sess:
        return {}

    http = SessionHTTPClient.from_session(sess)
    agent_name = sess.get("name") or agent_arg

    try:
        if name == "PreInvocation":
            sess = _ensure_t3_binding(sess, http, ev)
            try:
                http.state("busy")
            except SessionAuthError:
                return {}
            except Exception as e:
                _log("state-busy-error", agent_name, e)

            items = http.pull(wait=0)
            if not items:
                return {}
            ids = [i["id"] for i in items if "id" in i]
            try:
                http.delivered(ids)
            except Exception as e:
                _log("delivered-error", agent_name, ids, e)
            _log(agent_name, "PreInvocation injected", ids)
            return {"injectSteps": [{"systemMessage": {"systemMessage": _wrap(agent_name, items)}}]}

        if name == "Stop":
            # Strictly 0s non-blocking: also ensure T3 binding here because on turn 1
            # hub_register() runs AFTER PreInvocation, so Stop is the first hook that sees the session file.
            _ensure_t3_binding(sess, http, ev)
            try:
                http.state("idle")
            except SessionAuthError:
                pass
            except Exception as e:
                _log("state-idle-error", agent_name, e)
            return {}

        if name == "SessionStart":
            _ensure_t3_binding(sess, http, ev)
            return {}
    except SessionAuthError as e:
        _log("auth-error", agent_name, name, e)
    except Exception as e:
        _log("error", agent_name, name, e)
    return {}


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    agent_arg = args[0] if args else os.environ.get("HUB_AGENT", "")
    try:
        ev = json.load(sys.stdin)
    except Exception:
        ev = {}
    out = handle_event(ev if isinstance(ev, dict) else {}, agent_arg=agent_arg)
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
