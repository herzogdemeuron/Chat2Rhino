# -*- coding: utf-8 -*-
"""Pairing, the access check and the job loop, with a fake relay and Rhino."""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from chat2rhino import access, pairing, settings, store
from chat2rhino.connector import Connector
from chat2rhino.relay import RelayError


class StateDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        patcher = mock.patch.object(settings, "STATE_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        silent = mock.patch("chat2rhino.ui.tell")
        self.told = silent.start()
        self.addCleanup(silent.stop)


class AccessTests(unittest.TestCase):
    def job(self, tool, level=None, software="rhino"):
        request = {"software": software, "target_route": "/rhino_mcp/{0}/".format(tool)}
        if level:
            request["access"] = level
        return request

    def test_levels(self):
        self.assertIsNone(access.denial(self.job("get_context")))
        self.assertIsNotNone(access.denial(self.job("run_python")))  # no stamp = Reading
        self.assertIsNone(access.denial(self.job("run_python", "editing")))
        self.assertIsNotNone(access.denial(self.job("run_command", "editing")))
        self.assertIsNone(access.denial(self.job("run_command", "managing")))

    def test_unknown_tools_routes_and_applications_are_refused(self):
        self.assertIsNotNone(access.denial(self.job("_router_quit", "managing")))
        self.assertIsNotNone(access.denial(self.job("get_context/../run_command", "managing")))
        self.assertIsNotNone(access.denial({"software": "rhino", "target_route": "/other/", "access": "managing"}))
        self.assertIsNotNone(access.denial(self.job("get_context", "managing", software="revit")))
        self.assertIsNotNone(access.denial(self.job("run_python", "admin")))  # unknown = Reading


class FakeRelay(object):
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def request(self, method, path, payload=None, query=None, timeout=None):
        self.calls.append((path, payload))
        return self.answers.pop(0)


class PairingTests(StateDir):
    def test_pairs_and_keeps_the_token(self):
        relay = FakeRelay([
            (200, {"connect_url": "https://relay/connect?device=x", "poll_seconds": 2}),
            (200, {"status": "pending"}),
            (429, {"status": "slow_down"}),
            (200, {"status": "approved", "user_key": "user@example.com", "connector_token": "cwk_1",
                   "chat_opened": True}),
        ])
        opened = []
        chat_opened = pairing.pair("a.3dm", relay, open_page=opened.append,
                                   sleep=lambda s: None, clock=lambda: 0)
        self.assertTrue(chat_opened)
        self.assertEqual(["https://relay/connect?device=x"], opened)
        started = relay.calls[0][1]
        self.assertEqual("a.3dm", started["document"])
        self.assertEqual(started["device_id"], relay.calls[1][1]["device_id"])
        self.assertEqual(started["secret"], relay.calls[1][1]["secret"])
        self.assertEqual([], self.told.call_args_list)  # no messages when all goes well
        self.assertEqual(("user@example.com", "cwk_1", started["device_id"]), store.load_token())
        with open(store.path("token.json")) as handle:
            self.assertNotIn("cwk_1", handle.read())

    def test_gives_up_when_the_request_expires(self):
        relay = FakeRelay([
            (200, {"connect_url": "u"}),
            (404, {"status": "expired"}),
        ])
        self.assertIsNone(pairing.pair("", relay, open_page=lambda url: None, sleep=lambda s: None, clock=lambda: 0))
        self.assertIsNone(store.load_token())

    def test_outside_the_group_it_says_coming_soon_and_stops(self):
        relay = FakeRelay([
            (200, {"connect_url": "u"}),
            (403, {"status": "not_in_group", "user": "x@example.com", "group": "Chat2Rhino"}),
        ])
        self.assertIsNone(pairing.pair("", relay, open_page=lambda url: None, sleep=lambda s: None, clock=lambda: 0))
        self.assertEqual("Chat2Rhino is coming soon.", self.told.call_args[0][0])
        self.assertEqual("not_in_group", store.read_status()["state"])

    def test_an_unreachable_relay_is_reported(self):
        relay = mock.Mock(request=mock.Mock(side_effect=RelayError("down")))
        self.assertIsNone(pairing.pair("", relay, open_page=lambda url: None))
        self.assertIn("cannot reach", self.told.call_args[0][0])


class FakeRhino(object):
    def __init__(self, sessions=None):
        self.calls = []
        self.open = sessions or {10500: {"port": 10500, "pid": 1, "started": 5, "document_title": "box.3dm"}}

    def sessions(self):
        return dict(self.open)

    def probe(self):
        return {"available": True, "document_title": "box.3dm",
                "selected_port": 10500, "sessions": [{"port": 10500}]}

    def call(self, session, route, body, timeout, chat=""):
        self.calls.append((route, dict(body, target_port=session["port"])))
        return {"ok": True, "response": {"content": "done"}}


class ConnectorTests(StateDir):
    def setUp(self):
        StateDir.setUp(self)
        self.relay = mock.Mock()
        self.rhino = FakeRhino()
        self.connector = Connector("user@example.com", "cwk_1", "dev1", relay=self.relay, rhino=self.rhino)

    def job(self, job_id="j1", tool="get_context", level="reading"):
        return {"job_id": job_id, "request": {"software": "rhino", "access": level,
                                              "target_route": "/rhino_mcp/{0}/".format(tool),
                                              "body": {"x": 1}, "timeout_sec": 30}}

    def test_a_job_runs_once_and_a_second_delivery_gets_the_same_result(self):
        first = self.connector.run_job(self.job())
        self.assertTrue(first["ok"])
        self.assertEqual(first, self.connector.run_job(self.job()))
        self.assertEqual([("/rhino_mcp/get_context/", {"x": 1, "target_port": 10500})], self.rhino.calls)


    def test_a_job_above_the_level_never_reaches_rhino(self):
        result = self.connector.run_job(self.job(tool="run_command", level="editing"))
        self.assertEqual("permission_denied", result["error"])
        self.assertEqual([], self.rhino.calls)

    def test_the_heartbeat_reports_rhino_and_the_running_job(self):
        payload = self.connector._beat_payload()
        self.assertEqual(["rhino"], payload["software_available"])
        self.assertEqual("box.3dm", payload["hosts"]["rhino"]["document"]["title"])
        self.assertEqual(3, payload["connector_protocol"])
        self.assertIsNone(payload["current_job"])
        self.connector._current_job = {"job_id": "j1"}
        self.assertEqual("j1", self.connector._beat_payload()["hosts"]["rhino"]["current_job"]["job_id"])

    def test_a_refused_token_stops_the_connector(self):
        self.relay.beat.side_effect = RelayError("state answered 403", 403, {"error": "forbidden"})
        self.assertFalse(self.connector.beat())
        self.assertTrue(self.connector.revoked)
        self.assertTrue(self.connector.stopping.is_set())

    def test_a_member_removed_from_the_group_stops_without_losing_the_token(self):
        self.relay.beat.side_effect = RelayError("state answered 403", 403, {"error": "not_in_group"})
        self.connector.beat()
        self.assertTrue(self.connector.stopping.is_set())
        self.assertFalse(self.connector.revoked)
        self.relay.pull.return_value = None
        with mock.patch("chat2rhino.connector.CHECK_SECONDS", 0.01):
            self.connector.run()
        self.assertEqual("Chat2Rhino is coming soon.", self.told.call_args[0][0])
        self.assertEqual("not_in_group", store.read_status()["state"])

    def test_a_403_from_something_else_keeps_the_token(self):
        self.relay.beat.side_effect = RelayError("state answered 403", 403, {"text": "<html>blocked"})
        self.assertFalse(self.connector.beat())
        self.assertFalse(self.connector.revoked)

    def test_an_unexpected_error_does_not_end_the_job_loop(self):
        self.relay.pull.side_effect = [ValueError("odd"), {"job_id": "j9", "request": self.job()["request"]}]
        self.relay.complete.side_effect = lambda *a: self.connector.stopping.set()
        with mock.patch.object(settings, "POLL_SECONDS", 0.01), \
                mock.patch("chat2rhino.connector.ERROR_WAIT_SECONDS", 0.01):
            self.connector._job_loop()
        self.assertEqual(1, len(self.rhino.calls))

    def test_connected_only_once_the_relay_accepts_a_pull(self):
        self.relay.pull.side_effect = [RelayError("pull answered 403", 403, {"error": "not_in_group"})]
        store.write_status("starting")
        self.connector._job_loop()
        self.assertEqual("starting", store.read_status()["state"])

        member = Connector("user@example.com", "cwk_1", "dev1", relay=self.relay, rhino=self.rhino)
        def pull(worker, pulls=[]):  # accepted twice, then stop
            pulls.append(worker)
            if len(pulls) == 2:
                member.stopping.set()
        self.relay.pull.side_effect = pull
        with mock.patch.object(settings, "POLL_SECONDS", 0.01):
            member._job_loop()
        self.assertEqual("connected", store.read_status()["state"])

    def test_a_stop_request_ends_the_run(self):
        self.relay.pull.return_value = None
        store.request_stop()
        with mock.patch.object(settings, "BEAT_SECONDS", 0.05), mock.patch.object(settings, "POLL_SECONDS", 0.05), \
                mock.patch("chat2rhino.connector.CHECK_SECONDS", 0.05):
            self.assertEqual(0, self.connector.run())
        self.assertEqual("stopped", store.read_json("status.json")["state"])


class BindingTests(StateDir):
    TWO = {10500: {"port": 10500, "pid": 1, "started": 5, "document_title": "a.3dm"},
           10501: {"port": 10501, "pid": 2, "started": 6, "document_title": "b.3dm"}}

    def setUp(self):
        StateDir.setUp(self)
        self.rhino = FakeRhino(dict(self.TWO))
        self.opened = []
        self.connector = Connector("user@example.com", "cwk_1", "dev1", relay=mock.Mock(),
                                   rhino=self.rhino, open_page=self.opened.append)
        self.alive = {(1, 5), (2, 6)}
        self.connector.bindings._alive = lambda pid, started: (pid, started) in self.alive

    def call(self, chat, tool="get_context", **body):
        return self.connector._run({"software": "rhino", "target_route": "/rhino_mcp/%s/" % tool,
                                    "chat_id": chat, "body": dict(body)})

    def test_the_button_opens_a_chat_and_that_chat_takes_its_rhino(self):
        store.write_json("pending.json", {"clicks": [{"pid": 2, "started": 6, "document": "b.3dm", "clicked_at": int(time.time())}]})
        self.connector.open_waiting_chat()
        self.connector.open_waiting_chat()
        self.assertEqual(1, len(self.opened))
        self.assertIn("models=chat2rhino", self.opened[0])
        self.assertIn("b.3dm+is+connected+to+this+chat.", self.opened[0])
        self.assertTrue(self.call("chat-1")["ok"])
        self.assertEqual(10501, self.rhino.calls[-1][1]["target_port"])
        self.assertEqual({"clicks": []}, store.read_json("pending.json"))
        self.assertTrue(self.call("chat-1")["ok"])  # stays bound
        self.assertEqual(10501, self.rhino.calls[-1][1]["target_port"])

    def test_several_open_and_nothing_waiting_asks_for_a_choice(self):
        result = self.call("chat-2")
        self.assertEqual("choose_rhino_session", result["error"])
        self.assertEqual(["a.3dm", "b.3dm"], [s["document"] for s in result["response"]["sessions"]])
        self.assertTrue(self.call("chat-2", tool="rhino_bind", target_port=10500)["ok"])
        self.assertTrue(self.call("chat-2")["ok"])
        self.assertEqual(10500, self.rhino.calls[-1][1]["target_port"])
        listed = self.call("chat-2", tool="rhino_sessions")["response"]["sessions"]
        self.assertEqual([True, False], [s["this_chat"] for s in listed])
        switched = self.call("chat-2", tool="rhino_bind", target_port=10501)
        self.assertIn("new chat", switched["response"]["note"])

    def test_one_open_binds_by_itself_and_a_closed_rhino_is_reported(self):
        self.rhino.open = {10500: dict(self.TWO[10500])}
        self.assertTrue(self.call("chat-3")["ok"])
        self.rhino.open = {10500: dict(self.TWO[10500], pid=9, started=7)}  # port reused by another Rhino
        self.assertEqual("rhino_session_closed", self.call("chat-3")["error"])

    def test_a_clicked_rhino_that_is_not_connected_yet_is_not_replaced_by_another(self):
        self.rhino.open = {10500: dict(self.TWO[10500])}
        store.write_json("pending.json", {"clicks": [{"pid": 2, "started": 6, "document": "b.3dm"}]})
        self.assertEqual("rhino_not_ready", self.call("chat-5")["error"])
        self.rhino.open = dict(self.TWO)
        self.assertTrue(self.call("chat-5")["ok"])
        self.assertEqual(10501, self.rhino.calls[-1][1]["target_port"])

    def test_two_clicks_two_chats_each_binds_the_document_it_names(self):
        now = int(time.time())
        store.write_json("pending.json", {"clicks": [
            {"pid": 1, "started": 5, "document": "a.3dm", "clicked_at": now},
            {"pid": 2, "started": 6, "document": "b.3dm", "clicked_at": now}]})
        self.connector.open_waiting_chat()
        self.assertEqual(2, len(self.opened))
        self.assertEqual("choose_rhino_session", self.call("chat-a")["error"])
        self.assertTrue(self.call("chat-a", tool="rhino_bind", target_port=10500)["ok"])
        self.assertTrue(self.call("chat-b")["ok"])  # one click left: taken directly
        self.assertEqual(10501, self.rhino.calls[-1][1]["target_port"])
        self.call("chat-a")
        self.assertEqual(10500, self.rhino.calls[-1][1]["target_port"])

    def test_a_click_on_a_closed_rhino_no_longer_counts(self):
        store.write_json("pending.json", {"clicks": [{"pid": 2, "started": 999, "document": "old.3dm"}]})
        self.assertEqual("choose_rhino_session", self.call("chat-6")["error"])
        self.rhino.open = {10500: dict(self.TWO[10500])}
        self.assertTrue(self.call("chat-6")["ok"])

    def test_after_pairing_the_connect_page_opens_the_chat_not_the_connector(self):
        click = {"pid": 2, "started": 6, "document": "b.3dm", "clicked_at": int(time.time())}
        store.write_json("pending.json", {"clicks": [click]})
        self.connector.bindings.chat_opened(click)
        self.connector.open_waiting_chat()
        self.assertEqual([], self.opened)

    def test_an_old_click_opens_no_chat(self):
        store.write_json("pending.json", {"clicks": [{"pid": 2, "started": 6, "document": "b.3dm", "clicked_at": 1}]})
        self.connector.open_waiting_chat()
        self.assertEqual([], self.opened)

    def test_a_recent_click_of_a_running_rhino_keeps_the_connector_waiting(self):
        bindings = self.connector.bindings
        store.write_json("pending.json", {"clicks": [{"pid": 2, "started": 6, "clicked_at": int(time.time())}]})
        self.assertTrue(bindings.click_waiting())
        self.alive.discard((2, 6))
        self.assertFalse(bindings.click_waiting())
        self.alive.add((2, 6))
        store.write_json("pending.json", {"clicks": [{"pid": 2, "started": 6, "clicked_at": 1}]})
        self.assertFalse(bindings.click_waiting())

    def test_calls_without_a_chat_are_never_bound(self):
        self.rhino.open = {10500: dict(self.TWO[10500])}
        self.assertTrue(self.call("")["ok"])
        self.assertEqual({}, store.read_json("bindings.json"))

    def test_bindings_survive_a_restart(self):
        self.rhino.open = {10500: dict(self.TWO[10500])}
        self.call("chat-4")
        again = Connector("user@example.com", "cwk_1", "dev1", relay=mock.Mock(), rhino=self.rhino)
        self.rhino.open = dict(self.TWO)
        self.assertTrue(again._run({"software": "rhino", "target_route": "/rhino_mcp/get_context/",
                                    "chat_id": "chat-4", "body": {}})["ok"])


class BrowserTests(StateDir):
    def test_the_button_takes_the_page_or_the_connector_opens_it(self):
        from chat2rhino import browser

        opened = []
        browser.open_page("https://chat/a", fallback=opened.append, wait=0.05)
        self.assertEqual("https://chat/a", store.read_json("open.json")["url"])
        store.remove("open.json")  # what the button does
        time.sleep(0.15)
        self.assertEqual([], opened)
        browser.open_page("https://chat/b", fallback=opened.append, wait=0.05)
        time.sleep(0.15)
        self.assertEqual(["https://chat/b"], opened)
        self.assertEqual({}, store.read_json("open.json"))


if __name__ == "__main__":
    unittest.main()
