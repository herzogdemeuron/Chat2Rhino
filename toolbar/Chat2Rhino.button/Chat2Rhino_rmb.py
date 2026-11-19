# -*- coding: utf-8 -*-
"""Chat2Rhino button, right click: disconnect this Rhino.

Stops RhinoAI's server in this Rhino, so HdM Chat cannot reach it, and drops
its waiting click. Other Rhinos stay connected; the connector stops by itself
two minutes after no Rhino is connected. Chats that worked in this Rhino come
back after a left click in it. Runs in Rhino's IronPython or CPython.
"""

import os
import sys

import Rhino

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    HERE = r"C:\HdM-DT\RhinoToolbarExtensions\Chat2Rhino\toolbar\Chat2Rhino.button"
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import chat2rhino_button as common  # noqa: E402
try:
    reload(common)
except NameError:
    import importlib
    importlib.reload(common)

common.write_clicks(add_this_rhino=False)
stopped = common.stop_server()
if stopped == "stopped":
    Rhino.RhinoApp.WriteLine("Chat2Rhino: this Rhino is disconnected.")
elif stopped == "not_running":
    Rhino.RhinoApp.WriteLine("Chat2Rhino: this Rhino is not connected.")
else:
    Rhino.RhinoApp.WriteLine("Chat2Rhino: could not disconnect; close this Rhino to disconnect it.")
