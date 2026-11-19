# -*- coding: utf-8 -*-
"""The RhinoAI adapter."""

import json
import unittest

from chat2rhino.rhinoai import RhinoAI, document_title, parse_servicestate, to_job_result

SERVICESTATE = """\
Snapshot of HTTP service state (Server Session View):
-----------------------------------------------------

Request queue name: Request queue is unnamed.
    Version: 2.0
    State: Active
    Request queue 503 verbosity level: Basic
    Max requests: 1000
    Number of active processes attached: 1
    Process IDs:
        ID: 9216, image: C:\\Program Files\\Rhino 8\\System\\Rhino.exe
    Registered URLs:
        HTTP://LOCALHOST:10500/

Request queue name: Request queue is unnamed.
    Number of active processes attached: 1
    Process IDs:
        ID: 4400, image: C:\\Program Files\\Other\\other.exe
    Registered URLs:
        HTTP://LOCALHOST:10501/
"""


SESSION = {"port": 10500, "pid": 9216, "document_title": "box.3dm"}


class FakeRhino(object):
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, port, method, params, timeout):
        self.calls.append((port, method, params))
        return self.reply


def adapter(reply):
    rpc = FakeRhino(reply)
    return RhinoAI(
        user="user",
        registrations=lambda: {10500: (9216, "C:\\Program Files\\Rhino 8\\System\\Rhino.exe"),
                               10501: (4400, "C:\\Program Files\\Other\\other.exe")},
        titles=lambda: {9216: "box.3dm - Rhino 8 Commercial"}, rpc=rpc, started=lambda pid: 0,
        mine=lambda pid: True), rpc


class AdapterTests(unittest.TestCase):
    def test_servicestate(self):
        found = parse_servicestate(SERVICESTATE)
        self.assertEqual(found[10500], (9216, "C:\\Program Files\\Rhino 8\\System\\Rhino.exe"))
        self.assertEqual(found[10501][0], 4400)

    def test_any_port_a_rhino_registered_counts(self):
        rhino_adapter = RhinoAI(
            registrations=lambda: {10500: (1, "Rhino.exe"), 52993: (2, "C:\\Rhino 8\\System\\Rhino.exe")},
            titles=dict, rpc=FakeRhino({}), started=lambda pid: 0, mine=lambda pid: True)
        self.assertEqual([10500, 52993], sorted(rhino_adapter.sessions()))

    def test_only_rhino_sessions(self):
        rhino_adapter, _ = adapter({})
        self.assertEqual(list(rhino_adapter.sessions()), [10500])
        self.assertEqual(document_title("box.3dm - Rhino 8 Commercial"), "box.3dm")

    def test_call_forwards_the_tool_and_drops_port_fields(self):
        reply = {"result": {"content": [{"type": "text", "text": "{\"objects\": 3}"}]}}
        rhino_adapter, rpc = adapter(reply)
        result = rhino_adapter.call(SESSION, "/rhino_mcp/list_objects/", {"limit": 5, "target_port": 10500, "relay_target_port": 1}, 30, "c")
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], {"content": {"objects": 3}})
        self.assertEqual(rpc.calls, [(10500, "tools/call", {"name": "list_objects", "arguments": {"limit": 5}})])
        self.assertEqual(result["relay_meta"]["document_title"], "box.3dm")

    def test_run_python_is_tagged(self):
        rhino_adapter, rpc = adapter({"result": {"content": []}})
        rhino_adapter.call(SESSION, "/rhino_mcp/run_python/", {"script": "from Rhino.Geometry import Point3d\nprint(Point3d(0, 0, 0))"}, 30, "chat-1")
        script = rpc.calls[0][2]["arguments"]["script"]
        self.assertIn('SetUserString("chat2rhino_user", {0!r})'.format("user"), script)
        self.assertIn('SetUserString("chat2rhino_chat", {0!r})'.format("chat-1"), script)
        self.assertIn(repr("from Rhino.Geometry import Point3d\nprint(Point3d(0, 0, 0))"), script)
        compile(script, "<tagged>", "exec")

    def test_the_script_runs_exactly_as_written(self):
        rhino_adapter, rpc = adapter({"result": {"content": []}})
        source = 'from __future__ import annotations\ntext = """a\nb"""\n'
        rhino_adapter.call(SESSION, "/rhino_mcp/run_python/", {"script": source}, 30)
        namespace = {"__rhino_doc__": type("Doc", (), {"Objects": []})()}
        exec(rpc.calls[0][2]["arguments"]["script"], namespace)
        self.assertEqual("a\nb", namespace["text"])

    def test_the_most_recently_started_rhino_is_the_default(self):
        rpc = FakeRhino({"result": {"tools": []}})
        rhino_adapter = RhinoAI(
            registrations=lambda: {10500: (9000, "Rhino.exe"), 10501: (100, "Rhino.exe")},
            titles=dict, rpc=rpc, started=lambda pid: {9000: 1, 100: 2}[pid], mine=lambda pid: True)
        self.assertEqual(10501, rhino_adapter.probe()["selected_port"])

    def test_errors(self):
        rhino_adapter, _ = adapter({"error": {"code": -32602, "message": "unknown tool"}})
        result = rhino_adapter.call(SESSION, "/rhino_mcp/list_objects/", {}, 30)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown tool")
        rhino_adapter, _ = adapter({"result": {"isError": True, "content": [{"type": "text", "text": "bad"}]}})
        self.assertFalse(rhino_adapter.call(SESSION, "/rhino_mcp/list_objects/", {}, 30)["ok"])

    def test_no_session(self):
        rhino_adapter = RhinoAI(registrations=dict, titles=dict, rpc=FakeRhino({}), started=lambda pid: 0)
        self.assertEqual({}, rhino_adapter.sessions())
        self.assertFalse(rhino_adapter.probe()["available"])
        self.assertIsNone(rhino_adapter.probe()["selected_port"])

    def test_probe_leaves_the_document_alone(self):
        rhino_adapter, rpc = adapter({"result": {"tools": []}})
        state = rhino_adapter.probe()
        self.assertEqual([call[1] for call in rpc.calls], ["tools/list"])
        self.assertTrue(state["available"])
        self.assertEqual((state["selected_port"], state["document_title"]), (10500, "box.3dm"))

    def test_image_and_text(self):
        result = to_job_result({"content": [
            {"type": "text", "text": json.dumps({"viewport": "Perspective"})},
            {"type": "image", "mimeType": "image/jpeg", "data": "AAAA"}]})
        self.assertEqual(result, {"content": {"viewport": "Perspective"},
                                  "image_base64": "AAAA", "media_type": "image/jpeg"})


class OwnSessionTests(unittest.TestCase):
    def test_another_users_rhino_is_left_alone(self):
        rhino_adapter = RhinoAI(
            registrations=lambda: {10500: (9216, "Rhino.exe"), 10501: (777, "Rhino.exe")},
            titles=dict, rpc=FakeRhino({}), started=lambda pid: 0, mine=lambda pid: pid == 9216)
        self.assertEqual([10500], list(rhino_adapter.sessions()))


class RedirectTests(unittest.TestCase):
    def test_the_token_is_not_sent_to_a_redirect(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from chat2rhino.relay import Relay, RelayError

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/elsewhere" % self.server.server_address[1])
                self.end_headers()

            def log_message(self, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            relay = Relay("http://127.0.0.1:%d" % httpd.server_address[1], token="cwk_x")
            with self.assertRaises(RelayError):
                relay.pull("w")
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(["Bearer cwk_x"], seen)  # one request, no second one to the redirect


if __name__ == "__main__":
    unittest.main()
