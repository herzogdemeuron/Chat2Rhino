# -*- coding: utf-8 -*-
"""Chat2Rhino button, left click: a new HdM Chat chat for this Rhino.

Starts RhinoAI's server in this Rhino if it is not running yet (MCPStart),
marks this Rhino as waiting for a chat, and starts the Chat2Rhino connector in
the background, outside Rhino. The connector hands back the page to open (the
first time the page that connects this computer, else a new chat), and this
script opens it, so the browser comes to the front. On a computer that is not
connected yet, RhinoAI's server starts only once connecting succeeded, so
someone outside the test group never opens it. Runs in Rhino's IronPython or
CPython; Python 2.7 syntax on purpose.
"""

import os
import sys
import time

import Rhino
import scriptcontext
import System

TIMER_KEY = "chat2rhino_mcp_timer"
try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    HERE = r"C:\HdM-DT\RhinoToolbarExtensions\Chat2Rhino\toolbar\Chat2Rhino.button"
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import chat2rhino_button as common  # noqa: E402
try:
    reload(common)  # IronPython keeps modules between clicks; pick up an update
except NameError:
    import importlib
    importlib.reload(common)


def find_python():
    """Rhino 8's own Python first, else a Python on PATH (not the Store alias)."""
    candidates = [os.path.join(os.path.expanduser("~"), ".rhinocode", "py39-rh8", "pythonw.exe")]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if folder and "WindowsApps" not in folder:
            candidates.append(os.path.join(folder, "pythonw.exe"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def connector_running():
    """The running connector holds this mutex (chat2rhino/__main__.py)."""
    try:
        System.Threading.Mutex.OpenExisting("Local\\Chat2Rhino-connector").Dispose()
        return True
    except Exception:
        return False


def paired():
    return os.path.exists(os.path.join(common.STATE_DIR, "token.json"))


def start_mcp_once_connected(clicked, give_up=360):
    """Run MCPStart once the connector reports it is connected.

    A timer checks status.json every 2 s (Rhino may sit in the background
    while the user is in the browser) and hands MCPStart to Rhino's UI thread.
    One timer per Rhino: a new click replaces the old one.
    """
    previous = scriptcontext.sticky.pop(TIMER_KEY, None)
    if previous is not None:
        previous.Dispose()

    def check(_state):
        current = common.status()
        connected = current.get("updated_at", 0) >= clicked - 1 and current.get("state") == "connected"
        if connected or common.connector_gave_up(clicked) or time.time() - clicked > give_up:
            timer = scriptcontext.sticky.pop(TIMER_KEY, None)
            if timer is not None:
                timer.Dispose()
            if connected:
                Rhino.RhinoApp.InvokeOnUiThread(System.Action(common.start_server))

    scriptcontext.sticky[TIMER_KEY] = System.Threading.Timer(
        System.Threading.TimerCallback(check), None, 2000, 2000)


def open_handed_page(clicked, wait=6.0):
    """Open the page the connector hands over (chat2rhino/browser.py).

    Opened from here, inside Rhino right after the click, the browser comes to
    the front; the connector itself may not bring a window forward.
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        handed = common.read_json("open.json")
        url = handed.get("url") or ""
        if url.startswith("https://") and handed.get("at", 0) >= clicked - 1:
            try:
                os.remove(os.path.join(common.STATE_DIR, "open.json"))
            except OSError:
                return  # the connector's fallback is opening it
            info = System.Diagnostics.ProcessStartInfo(url)
            info.UseShellExecute = True
            System.Diagnostics.Process.Start(info)
            return
        if common.connector_gave_up(clicked):
            return  # nothing will be handed over
        time.sleep(0.25)


def start_connector(python, root):
    info = System.Diagnostics.ProcessStartInfo(python, "-m chat2rhino start")
    info.WorkingDirectory = root
    info.UseShellExecute = False
    info.CreateNoWindow = True
    # Rhino's own Python settings must not leak into the connector's Python.
    for name in ("PYTHONHOME", "PYTHONPATH"):
        if info.EnvironmentVariables.ContainsKey(name):
            info.EnvironmentVariables.Remove(name)
    System.Diagnostics.Process.Start(info)


def main():
    clicked = time.time()
    python = find_python()
    if python is None:
        Rhino.RhinoApp.WriteLine("Chat2Rhino: no Python found. Run any Python script in Rhino 8 "
                                 "once (ScriptEditor), then click Chat2Rhino again.")
        return
    running = connector_running()
    current = common.status()
    # RhinoAI's server opens at once only for a computer that is connected and allowed.
    if paired() and current.get("state") != "not_in_group":
        common.start_server()
    else:
        start_mcp_once_connected(clicked)
    common.write_clicks(add_this_rhino=True)
    start_connector(python, os.path.dirname(os.path.dirname(HERE)))  # the repository
    if running and current.get("state") == "connected":
        Rhino.RhinoApp.WriteLine("Chat2Rhino: a new chat for this Rhino opens in your browser.")
    else:
        Rhino.RhinoApp.WriteLine("Chat2Rhino: starting. The first time, a page opens in your "
                                 "browser to connect this computer; then a new chat for this Rhino.")
    Rhino.RhinoApp.WriteLine("Chat2Rhino: if Rhino says MCPStart is an unknown command, install "
                             "RhinoAI from the Package Manager.")
    open_handed_page(clicked)


main()
