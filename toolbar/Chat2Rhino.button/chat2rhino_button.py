# -*- coding: utf-8 -*-
"""What the two Chat2Rhino button scripts share (Rhino's IronPython or CPython).

The scripts cannot import the chat2rhino package (it needs Python 3), so the
state folder layout is repeated here; keep it the same as chat2rhino/store.py.
"""

import io
import json
import os
import time

import Rhino
import System

STATE_DIR = os.environ.get("CHAT2RHINO_STATE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "HdM", "Chat2Rhino")
_FLAGS = (System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public
          | System.Reflection.BindingFlags.NonPublic)


def path(name):
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR)
    return os.path.join(STATE_DIR, name)


def read_json(name):
    try:
        with io.open(os.path.join(STATE_DIR, name), encoding="utf-8") as handle:
            data = json.loads(handle.read())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def status():
    return read_json("status.json")


def connector_gave_up(since):
    """The connector stopped after *since* without connecting (refused, or pairing failed)."""
    current = status()
    return (current.get("updated_at", 0) >= since - 1
            and current.get("state") in ("not_in_group", "not_connected"))


# -- RhinoAI's server in this Rhino --
# RhinoAI 0.4.0 has MCPStart (which restarts a running server on a new port)
# but no stop command. Its host class (internal, Rhino.AI.RhinoAIHost) has
# HasStarted(doc) and Stop(doc); reached through reflection, so a later
# RhinoAI without them (or with other signatures) only makes these return None.

_HOST = []  # RhinoAIHost, once found (RhinoAI loads when its first command runs)


def _host():
    if not _HOST:
        for assembly in System.AppDomain.CurrentDomain.GetAssemblies():
            try:
                found = assembly.GetType("Rhino.AI.RhinoAIHost")
            except Exception:
                found = None
            if found is not None:
                _HOST.append(found)
                break
    return _HOST[0] if _HOST else None


def _call(name):
    try:
        host = _host()
        method = host.GetMethod(name, _FLAGS) if host is not None else None
        doc = Rhino.RhinoDoc.ActiveDoc
        if method is None or doc is None:
            return None
        return method.Invoke(None, System.Array[System.Object]([doc]))
    except Exception:
        return None


def server_running():
    """True or False; None when RhinoAI is not loaded or has no HasStarted."""
    running = _call("HasStarted")
    return None if running is None else bool(running)


def start_server():
    """MCPStart, unless this Rhino's server already runs (a restart would change its port)."""
    if server_running():
        return
    Rhino.RhinoApp.RunScript("_-MCPStart _Enter", False)


def stop_server():
    """"stopped", "not_running" or "unknown" (RhinoAI cannot be asked or did not stop)."""
    running = server_running()
    if running is False:
        return "not_running"
    if running:
        _call("Stop")
        if server_running() is False:
            return "stopped"
    return "unknown"


# -- clicks waiting for a chat (chat2rhino/sessions.py) --

def this_rhino():
    me = System.Diagnostics.Process.GetCurrentProcess()
    return int(me.Id), str(me.StartTime.ToFileTimeUtc()).rstrip("L")


def _click_json(pid, started, document, clicked_at):
    # Written by hand: IronPython's json cannot encode the .NET Int64 start time.
    return u'{{"pid": {0}, "started": {1}, "document": {2}, "clicked_at": {3}}}'.format(
        int(pid), str(started).rstrip("L"), json.dumps(document), int(clicked_at))


def write_clicks(add_this_rhino):
    """Drop this Rhino's waiting click and, with *add_this_rhino*, add a new one."""
    pid, started = this_rhino()
    clicks = [_click_json(c["pid"], c.get("started", 0), c.get("document") or "Untitled",
                          c.get("clicked_at", 0))
              for c in (read_json("pending.json").get("clicks") or [])[-9:]
              if not (int(c.get("pid", 0)) == pid and str(c.get("started", 0)).rstrip("L") == started)]
    if add_this_rhino:
        doc = Rhino.RhinoDoc.ActiveDoc
        clicks.append(_click_json(pid, started, (doc.Name if doc else None) or "Untitled", time.time()))
    target = path("pending.json")
    with io.open(target + ".new", "w", encoding="utf-8") as handle:
        handle.write(u'{"clicks": [' + u", ".join(clicks) + u"]}")
    # Replace in one step: the connector never reads a half-written file.
    if os.path.exists(target):
        System.IO.File.Replace(target + ".new", target, None)
    else:
        System.IO.File.Move(target + ".new", target)
