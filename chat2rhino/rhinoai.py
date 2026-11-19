# -*- coding: utf-8 -*-
"""Rhino through McNeel's RhinoAI MCP server.

RhinoAI serves MCP as plain JSON-RPC POSTs on http://localhost:<port>/, one
port per document (10500, or any free port); no handshake or session is needed. This adapter
forwards a job's route (``/rhino_mcp/<tool>/``) as ``tools/call`` of that
tool and maps the MCP result to a job result. Standard library only.

Checked on RhinoAI 0.4.0 and Rhino 8.35:

- The port is owned by HTTP.SYS (pid 4), so the process behind it is read from
  ``netsh http show servicestate`` (no admin rights needed) instead of the TCP
  table, and only a Rhino.exe registration is trusted.
- No RhinoAI tool names the document, so its title comes from the Rhino main
  window, without running code in Rhino.
- ``run_python`` already runs as one undo step ("ScriptEditor"). The adapter
  wraps the script so the objects it adds carry ``chat2rhino_user`` and
  ``chat2rhino_chat`` user strings; one ``_Undo`` removes objects and tags.
"""

import json
import ntpath
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from .access import tool_of

PROCESS_NAMES = ("rhino.exe",)
STATUS_TIMEOUT = 3.0

_URL_RE = re.compile(r"HTTP://(?:LOCALHOST|127\.0\.0\.1):(\d+)(?::127\.0\.0\.1)?/\s*$", re.IGNORECASE)
_PROCESS_RE = re.compile(r"\bID:\s*(\d+),\s*[^:]+:\s*(.+\.exe)\s*$", re.IGNORECASE)

# The model's script runs unchanged (compiled as its own source, so string
# literals and ``from __future__`` imports stay intact); the objects it added
# are collected first and tagged afterwards.
TAG_TEMPLATE = """\
__c2r_doc = __rhino_doc__
__c2r_before = set(o.Id for o in __c2r_doc.Objects)
try:
    exec(compile({script!r}, "<run_python>", "exec"), globals())
finally:
    __c2r_new = [o for o in __c2r_doc.Objects if o.Id not in __c2r_before]
    for __c2r_obj in __c2r_new:
        __c2r_attrs = __c2r_obj.Attributes.Duplicate()
        __c2r_attrs.SetUserString("chat2rhino_user", {user!r})
        __c2r_attrs.SetUserString("chat2rhino_chat", {chat!r})
        __c2r_doc.Objects.ModifyAttributes(__c2r_obj, __c2r_attrs, True)
"""


def parse_servicestate(output):
    """{port: (pid, image)} of HTTP.SYS request queues registered on localhost."""
    found = {}
    processes = []
    for line in output.splitlines():
        if not line.strip():
            continue
        if re.match(r"^\S", line):
            processes = []  # a new request queue block starts at column 0
        process = _PROCESS_RE.search(line)
        if process:
            processes.append((int(process.group(1)), process.group(2).strip()))
            continue
        url = _URL_RE.search(line)
        if url and len(processes) == 1:
            found[int(url.group(1))] = processes[0]
    return found


def http_sys_registrations():
    if os.name != "nt":
        return {}
    try:
        output = subprocess.run(
            ["netsh", "http", "show", "servicestate", "view=requestq", "verbose=no"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("mbcs", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return parse_servicestate(output)


def rhino_window_titles():
    """{pid: title} of visible top-level Rhino main windows, in one pass."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd) if user32.IsWindowVisible(hwnd) else 0
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if " - Rhino" in buffer.value:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                titles.setdefault(owner.value, buffer.value)
        return True

    user32.EnumWindows(visit, 0)
    return titles


def document_title(window):
    """'Untitled - Rhino 8 Commercial' -> 'Untitled'."""
    return window.rsplit(" - Rhino", 1)[0].strip() if " - Rhino" in window else ""


def process_started(pid):
    """When process *pid* started (Windows FILETIME), or 0 when unknown."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return 0
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def same_session(pid):
    """Whether *pid* runs in this process's Windows session (so: this user's desktop)."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    theirs, ours = wintypes.DWORD(), wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(theirs)):
        return False
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(ours))
    return theirs.value == ours.value


def _newest(sessions):
    """The Rhino the heartbeat describes: the most recently started one."""
    return max(sessions, key=lambda item: (item.get("started", 0), item["pid"])) if sessions else None


def _rpc(port, method, params, timeout):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    request = urllib.request.Request(
        "http://localhost:{0}/".format(port), data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def to_job_result(mcp_result):
    """Map an MCP tools/call result to a job response.

    Text parts that are JSON are parsed; the first image becomes
    ``image_base64`` / ``media_type``, which the relay publishes and, for
    Rhino, also hands to the model.
    """
    texts, image = [], None
    for part in mcp_result.get("content") or []:
        if part.get("type") == "text":
            text = part.get("text", "")
            try:
                texts.append(json.loads(text))
            except ValueError:
                texts.append(text)
        elif part.get("type") == "image" and image is None:
            image = {"image_base64": part.get("data", ""), "media_type": part.get("mimeType") or "image/png"}
    response = {"content": texts[0] if len(texts) == 1 else texts}
    if image:
        response.update(image)
    return response


def wrap_with_tags(script, user, chat):
    """Run *script* unchanged and tag every object it adds."""
    return TAG_TEMPLATE.format(script=script, user=user, chat=chat)


class RhinoAI(object):
    def __init__(self, user="", registrations=http_sys_registrations,
                 titles=rhino_window_titles, rpc=_rpc, started=process_started, mine=same_session):
        self._user = user  # the connector's user: it serves one user only
        self._registrations = registrations
        self._titles = titles
        self._rpc = rpc
        self._started = started
        self._mine = mine

    def sessions(self):
        """{port: session} of RhinoAI servers that belong to a live Rhino process.

        Any localhost port a Rhino.exe registered counts: RhinoAI starts at
        10500 but takes a free port of its own (52993, seen) when that is taken.
        """
        titles = None
        out = {}
        for port, owner in sorted(self._registrations().items()):
            if ntpath.basename(owner[1]).lower() not in PROCESS_NAMES:
                continue
            if not self._mine(owner[0]):
                continue  # another user's Rhino on a shared computer
            if titles is None:
                titles = self._titles()
            pid = owner[0]
            out[port] = {"port": port, "pid": pid, "started": self._started(pid),
                         "document_title": document_title(titles.get(pid, ""))}
        return out

    def probe(self):
        """The running sessions and the default one, without touching any document."""
        sessions = [item for _port, item in sorted(self.sessions().items())]
        for item in sessions:
            try:
                item["available"] = "result" in self._rpc(item["port"], "tools/list", {}, STATUS_TIMEOUT)
            except (OSError, ValueError, urllib.error.URLError):
                item["available"] = False
        current = _newest(sessions) or {}
        return {
            "available": bool(current.get("available")),
            "document_title": current.get("document_title", ""),
            "selected_port": current.get("port"),
            "sessions": sessions,
        }

    def call(self, session, route, body, timeout, chat=""):
        """Run one tool in *session* (an entry of ``sessions()``) for *chat*."""
        # A session is chosen by binding; port fields a request may carry are dropped.
        body = dict((key, value) for key, value in (body or {}).items()
                    if key != "port" and not key.endswith("target_port"))
        port = session["port"]
        meta = {"execution_mode": "rhinoai_mcp", "selected_port": port}
        tool = tool_of(route)
        if not tool:
            return {"ok": False, "error": "unknown_route", "response": {"route": route}, "relay_meta": meta}
        if tool == "run_python" and isinstance(body.get("script"), str):
            body["script"] = wrap_with_tags(body["script"], self._user, chat)
        started = time.time()
        try:
            reply = self._rpc(port, "tools/call", {"name": tool, "arguments": body}, timeout)
        except (OSError, ValueError, urllib.error.URLError) as error:
            return {"ok": False, "error": "rhinoai_unreachable: {0}".format(error), "relay_meta": meta}
        meta.update({"document_title": session.get("document_title", ""),
                     "elapsed_ms": round((time.time() - started) * 1000)})
        if "error" in reply:
            error = reply.get("error") or {}
            return {"ok": False, "http_status": 400, "error": error.get("message", "mcp error"),
                    "response": error, "relay_meta": meta}
        result = reply.get("result") or {}
        return {"ok": not result.get("isError"), "http_status": 200,
                "response": to_job_result(result), "relay_meta": meta}
