# -*- coding: utf-8 -*-
"""Open a page in the user's browser, in front of Rhino.

Windows lets only the program the user is working in bring a window to the
front, and the connector runs in the background. So the connector hands the
page to the Chat2Rhino button, which is running inside Rhino right after the
click and opens it (``open.json``; see the button script). If the button does
not take it within ``HANDOFF_SECONDS`` (it waits a few seconds only), the
connector opens it itself, behind Rhino.
"""

import threading
import time
import webbrowser

from . import store
from .store import LOG

HANDOFF_SECONDS = 8.0


def open_page(url, fallback=webbrowser.open, wait=HANDOFF_SECONDS):
    store.write_json("open.json", {"url": url, "at": time.time()})

    def fall_back():
        if store.read_json("open.json").get("url") == url:
            store.remove("open.json")
            LOG.info("The button did not open the page; opening it in the background")
            fallback(url)

    timer = threading.Timer(wait, fall_back)
    timer.daemon = True
    timer.start()
