# -*- coding: utf-8 -*-
"""Which Rhino each chat works in.

Every HdM Chat chat is bound to one Rhino, on its first Rhino tool call:

- to the Rhino whose Chat2Rhino button was clicked and is still waiting for a
  chat (``pending.json``, written by the button); each click opens a new chat
  for its Rhino, so normally that new chat takes it;
- when several clicks wait, the tool answers with the list and the model binds
  the document its chat's first line names (``rhino_bind``);
- with no click waiting, to the only Rhino that is open, else the model asks
  the user.

A Rhino is identified by its process (id and start time), so a process id
reused by another Rhino never inherits a binding or a click. Bindings are
kept in ``bindings.json`` (the most recently used 500), so a restarted
connector still knows them. A call without a chat id is resolved each time
and never bound.
"""

import collections
import os
import threading
import time

from . import store
from .rhinoai import process_started

MAX_BINDINGS = 500
OPEN_CHAT_SECONDS = 600  # a click older than this opens no chat (a connector started much later)


def _key(entry):
    """A Rhino process: its id and start time (sessions, clicks and bindings alike)."""
    return [entry.get("pid"), entry.get("started", 0)]


def _click_id(click):
    return (click.get("pid"), click.get("started", 0), click.get("clicked_at", 0))


def _title(session):
    return session.get("document_title") or "Untitled"


def _find(sessions, key):
    return next((s for s in sessions.values() if _key(s) == key), None)


def describe(sessions, bound=None):
    """The open documents as the model sees them."""
    return [{"target_port": port, "document": _title(item),
             "this_chat": bool(bound) and _key(item) == bound}
            for port, item in sorted(sessions.items())]


def refusal(error, message, sessions, bound=None):
    return {"ok": False, "error": error,
            "response": {"status": error, "message": message,
                         "sessions": describe(sessions, bound)}}


class Bindings(object):
    """Bindings and the waiting click, behind one lock (the button writes
    ``pending.json``; the job loop and the main loop both use it)."""

    def __init__(self, alive=lambda pid, started: process_started(pid) == started):
        self._alive = alive
        self._opened = set()  # clicks whose chats are open
        self._click_mtime = None
        self._lock = threading.Lock()
        data = store.read_json("bindings.json")
        self._chats = collections.OrderedDict(
            (chat, entry) for chat, entry in data.items() if isinstance(entry, dict))

    def _save_locked(self):
        while len(self._chats) > MAX_BINDINGS:
            self._chats.popitem(last=False)
        store.write_json("bindings.json", self._chats)

    def _bind_locked(self, chat, session):
        self._chats[chat] = {"session": _key(session), "document": _title(session)}
        self._chats.move_to_end(chat)
        self._save_locked()

    def bound(self, chat):
        with self._lock:
            entry = self._chats.get(chat)
            return entry["session"] if entry else None

    def _clicks_locked(self):
        """Clicks waiting for a chat, whose Rhino still runs."""
        data = store.read_json("pending.json")
        clicks = data.get("clicks") if isinstance(data.get("clicks"), list) else []
        return [c for c in clicks if isinstance(c, dict) and c.get("pid")
                and self._alive(c["pid"], c.get("started", 0))]

    def _take_click_locked(self, session):
        """The click of *session* has its chat: it no longer waits."""
        data = store.read_json("pending.json")
        clicks = data.get("clicks") if isinstance(data.get("clicks"), list) else []
        left = [c for c in clicks if isinstance(c, dict) and _key(c) != _key(session)]
        if len(left) != len(clicks):
            store.write_json("pending.json", {"clicks": left})

    def chat_opened(self, click):
        """*click* got its chat elsewhere (the connect page opens it after pairing)."""
        with self._lock:
            self._opened.add(_click_id(click))

    def chats_to_open(self):
        """Documents of new clicks whose chats are not open yet (each click once)."""
        try:
            mtime = os.stat(store.path("pending.json")).st_mtime
        except OSError:
            return []
        if mtime == self._click_mtime:
            return []  # read only when the button wrote a new click
        self._click_mtime = mtime
        with self._lock:
            new = [c for c in self._clicks_locked() if _click_id(c) not in self._opened
                   and time.time() - c.get("clicked_at", 0) <= OPEN_CHAT_SECONDS]
            self._opened.update(_click_id(c) for c in new)
            return [c.get("document") or "Untitled" for c in new]

    def resolve(self, chat, sessions):
        """``(session, None)`` for this chat's Rhino, or ``(None, failed result)``."""
        if not sessions:
            return None, refusal("rhino_unavailable", "No Rhino is connected. Ask the user to click "
                                 "Chat2Rhino in Rhino.", sessions)
        with self._lock:
            entry = self._chats.get(chat) if chat else None
            if entry:
                session = _find(sessions, entry["session"])
                if session:
                    self._chats.move_to_end(chat)
                    return session, None
                return None, refusal(
                    "rhino_session_closed",
                    "The Rhino this chat works in ({0}) is closed or no longer connected. Ask the "
                    "user to open it and click Chat2Rhino there, or to start a new chat for another "
                    "document.".format(entry.get("document") or "Untitled"), sessions)
            clicks = self._clicks_locked()
            if len(clicks) > 1:
                return None, refusal(
                    "choose_rhino_session",
                    "Chat2Rhino was clicked in several Rhinos, each with a chat of its own. Call "
                    "rhino_bind with the target_port of the document this chat's first message "
                    "names; ask the user only if several documents have that name.", sessions)
            if clicks:
                chosen = _find(sessions, _key(clicks[0]))
                if not chosen:
                    return None, refusal(
                        "rhino_not_ready",
                        "The Rhino where the user clicked Chat2Rhino ({0}) is not connected yet. "
                        "Wait a few seconds and try again; if it stays so, ask the user to click "
                        "Chat2Rhino in that Rhino again.".format(clicks[0].get("document") or "Untitled"),
                        sessions)
            elif len(sessions) == 1:
                chosen = next(iter(sessions.values()))
            else:
                return None, refusal(
                    "choose_rhino_session",
                    "Several Rhino documents are open and this chat works in none yet. Ask the user "
                    "which one (or take the one they named), then call rhino_bind with its "
                    "target_port.", sessions)
            if chat:
                self._bind_locked(chat, chosen)
                self._take_click_locked(chosen)
            return chosen, None

    # -- the two tools the connector serves itself --

    def list(self, chat, sessions):
        return {"ok": True, "response": {"sessions": describe(sessions, self.bound(chat))}}

    def bind(self, chat, sessions, port):
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = None
        if port not in sessions or not chat:
            return refusal("unknown_rhino_session", "Use a target_port from rhino_sessions.", sessions,
                           self.bound(chat))
        with self._lock:
            switched = chat in self._chats
            self._bind_locked(chat, sessions[port])
            self._take_click_locked(sessions[port])
        note = ("Switched. Tell the user that one chat per document is easier to follow: clicking "
                "Chat2Rhino in a Rhino opens a new chat for it.") if switched else "Bound."
        return {"ok": True, "response": {"document": _title(sessions[port]),
                                         "target_port": port, "note": note}}
