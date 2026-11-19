# -*- coding: utf-8 -*-
"""Where things are, and how often the connector talks to the relay."""

import os

RELAY_URL = os.environ.get(
    "CHAT2RHINO_RELAY_URL", "https://chatbot.herzogdemeuron.com/chat2rhino").rstrip("/")
STATE_DIR = os.environ.get("CHAT2RHINO_STATE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "HdM", "Chat2Rhino")

POLL_SECONDS = 2.0        # ask for the next job
BEAT_SECONDS = 10.0       # tell the relay this connector is alive
PROBE_SECONDS = 30.0      # look again at the running Rhino sessions
RHINO_GONE_SECONDS = 120  # stop when no Rhino has been running this long
RELAY_TIMEOUT = 20.0

# Where the button's new chat opens, and with which model.
CHAT_URL = os.environ.get("CHAT2RHINO_CHAT_URL", "https://chatbot.herzogdemeuron.com").rstrip("/")
MODEL_ID = "chat2rhino"
