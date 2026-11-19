# -*- coding: utf-8 -*-
"""Serve the user's Rhino jobs from the relay.

Three loops:

- jobs: pull the next Rhino job, check it (``access``), run it through
  RhinoAI, hand the result back;
- heartbeat: tell the relay every few seconds that this connector is alive,
  which Rhino sessions run and which job is running (that also keeps the
  job's lease, so the relay does not hand it out again);
- the main loop: stops on a stop request, a revoked token, or when no Rhino
  has been running for a while.

A job the relay delivers again (handing back its result failed) is not run
again: the result is sent again. On stop, a running job finishes
and reports first (jobs run at most a few minutes).
"""

import collections
import os
import threading
import time
import urllib.parse

from . import access, browser, settings, store, ui
from .relay import Relay, RelayError
from .rhinoai import RhinoAI
from .sessions import Bindings
from .store import LOG

PROTOCOL = 3  # the relay's digest and job protocol this connector speaks
CHECK_SECONDS = 1  # the main loop: stop request, a new click, Rhino still running (cached)
ERROR_WAIT_SECONDS = 10  # after a failed pull (relay down, access check unavailable, ...)
JOB_GRACE_SECONDS = 240  # longest job timeout (180 s) plus time to report


class Connector(object):
    def __init__(self, user_key, token, device_id, relay=None, rhino=None, open_page=browser.open_page):
        self.user_key = user_key
        self.device_id = device_id
        self.worker_id = "chat2rhino-" + device_id
        self.relay = relay or Relay(token=token)
        self.rhino = rhino or RhinoAI(user=user_key)
        self.bindings = Bindings()
        self._open_page = open_page
        self.stopping = threading.Event()
        self.revoked = False
        self.not_in_group = False
        self._lock = threading.Lock()
        self._rhino_state = {}
        self._probed_at = 0.0
        self._current_job = None
        self._results = collections.OrderedDict()  # job id -> result, the last few
        self._beat_seq = 0

    # -- Rhino --

    def _probe(self):
        """The cached Rhino state; one thread refreshes it when it is old."""
        now = time.time()
        with self._lock:
            if now - self._probed_at < settings.PROBE_SECONDS:
                return self._rhino_state
            self._probed_at = now  # the other thread keeps the cached state meanwhile
        try:
            probe = self.rhino.probe()
        except Exception as error:  # a probe must never stop the connector
            LOG.warning("Rhino probe failed: %s", error)
            probe = {}
        available = bool(probe.get("available"))
        state = {
            "available": available,
            "document_open": available,
            "document": {"title": probe.get("document_title", "")} if available else {},
            "sessions": list(probe.get("sessions") or []),
            "selected_port": probe.get("selected_port"),
            "rhino_transport": "rhinoai_mcp",
        }
        with self._lock:
            self._rhino_state = state
        return state

    def rhino_running(self):
        return bool(self._probe().get("sessions"))

    # -- heartbeat --

    def _beat_payload(self):
        state = dict(self._probe())
        with self._lock:
            job = self._current_job
            self._beat_seq += 1
            seq = self._beat_seq
        state["busy"] = job is not None
        state["current_job"] = job
        return {
            "user_key": self.user_key,
            "worker_id": self.worker_id,
            "device_id": self.device_id,
            "connector_protocol": PROTOCOL,
            "software_available": ["rhino"] if state["available"] else [],
            "hosts": {"rhino": state},
            "busy": job is not None,
            "current_job": job,
            "beat_seq": seq,
            "heartbeat_interval": settings.BEAT_SECONDS,
        }

    def beat(self):
        try:
            self.relay.beat(self._beat_payload())
            return True
        except RelayError as error:
            self._relay_refused(error)
            LOG.warning("Heartbeat failed: %s", error)
        except Exception:
            LOG.exception("Heartbeat failed")
        return False

    def _heartbeat_loop(self):
        while not self.stopping.is_set():
            self.beat()
            self.stopping.wait(settings.BEAT_SECONDS)

    def _relay_refused(self, error):
        if error.status == 403 and error.body.get("error") == "not_in_group":
            if not self.not_in_group:
                LOG.info("%s is not in the access group; stopping", self.user_key)
                self.not_in_group = True
                self.stopping.set()
            return
        if error.token_refused:
            LOG.warning("The relay refused this computer's token; pairing again next time")
            self.revoked = True
            self.stopping.set()

    # -- jobs --

    def run_job(self, job):
        """Run one job and return its result (never raises)."""
        job_id = str(job.get("job_id") or "")
        request = job.get("request") or {}
        with self._lock:
            if job_id in self._results:
                LOG.info("Job %s delivered again; sending its result again", job_id)
                return self._results[job_id]
            self._current_job = {"job_id": job_id, "route": request.get("target_route", ""),
                                 "started_at_epoch": time.time()}
        result = None
        try:
            result = access.denial(request) or self._run(request)
        except Exception as error:
            LOG.exception("Job %s failed", job_id)
            result = {"ok": False, "error": str(error), "error_code": "connector_execution_failed"}
        finally:
            with self._lock:
                self._results[job_id] = result
                while len(self._results) > 16:
                    self._results.popitem(last=False)
                self._current_job = None
        return result

    def _run(self, request):
        """Run an allowed job in the Rhino its chat is bound to."""
        route = request.get("target_route", "")
        body = dict(request.get("body") or {})
        chat = str(request.get("chat_id") or "")
        tool = access.tool_of(route)
        sessions = self.rhino.sessions()
        if tool == "rhino_sessions":
            return self.bindings.list(chat, sessions)
        if tool == "rhino_bind":
            return self.bindings.bind(chat, sessions, body.get("target_port"))
        session, refused = self.bindings.resolve(chat, sessions)
        if refused:
            return refused
        return self.rhino.call(session, route, body, float(request.get("timeout_sec") or 60), chat)

    def open_waiting_chat(self):
        """Open a new chat for the Rhino whose button was just clicked."""
        try:
            for document in self.bindings.chats_to_open():
                # The connect page builds the same link for the first chat (relay connect.js).
                line = "{0} is connected to this chat.".format(document)
                self._open_page("{0}/?{1}".format(settings.CHAT_URL, urllib.parse.urlencode(
                    {"models": settings.MODEL_ID, "q": line})))
                LOG.info("Opened a new chat for %s", document)
        except Exception:
            LOG.exception("Opening a chat failed")

    def _job_loop(self):
        while not self.stopping.is_set():
            try:
                job = self.relay.pull(self.worker_id)
            except RelayError as error:
                self._relay_refused(error)
                LOG.warning("Pull failed: %s", error)
                self.stopping.wait(ERROR_WAIT_SECONDS)
                continue
            except Exception:
                LOG.exception("Pull failed")
                self.stopping.wait(ERROR_WAIT_SECONDS)
                continue
            if not isinstance(job, dict) or not job.get("job_id"):
                self.stopping.wait(settings.POLL_SECONDS)
                continue
            result = self.run_job(job)
            LOG.info("Job %s %s: %s", job["job_id"], (job.get("request") or {}).get("target_route"),
                     "ok" if result.get("ok") else "failed")
            try:
                self.relay.complete(job["job_id"], self.worker_id, result)
            except Exception as error:
                LOG.warning("Complete failed for %s: %s", job["job_id"], error)

    # -- main --

    def run(self):
        store.write_status("connected", user_key=self.user_key)
        LOG.info("Connector running for %s (pid %s)", self.user_key, os.getpid())
        threads = [threading.Thread(target=target, name=name, daemon=True)
                   for name, target in (("heartbeat", self._heartbeat_loop), ("jobs", self._job_loop))]
        for thread in threads:
            thread.start()
        rhino_seen = time.time()
        self.open_waiting_chat()
        while not self.stopping.wait(CHECK_SECONDS):
            self.open_waiting_chat()
            if store.stop_requested():
                LOG.info("Stop requested")
                store.write_status("stopping", user_key=self.user_key)
                break
            if self.rhino_running():
                rhino_seen = time.time()
            elif time.time() - rhino_seen > settings.RHINO_GONE_SECONDS:
                LOG.info("No Rhino with RhinoAI's server for %ss; stopping", settings.RHINO_GONE_SECONDS)
                break
        self.stopping.set()
        for thread in threads:
            thread.join(JOB_GRACE_SECONDS)
        if self.not_in_group:
            store.write_status("not_in_group", user_key=self.user_key)
            ui.tell(ui.COMING_SOON)
        else:
            store.write_status("revoked" if self.revoked else "stopped", user_key=self.user_key)
        return 0
