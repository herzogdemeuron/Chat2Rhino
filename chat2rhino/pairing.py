# -*- coding: utf-8 -*-
"""Pair this computer with the user's HdM Chat account.

The relay gets a one-time device id and a secret; the browser opens the
relay's connect page, where the user signs in with Microsoft and clicks
Connect; the page then opens the new chat for the clicked Rhino's document
itself. Meanwhile this polls with the secret; once approved the relay hands
over a connector token, which only the holder of the secret can receive.
Gives up when the relay says the request expired (after five minutes).
Messages appear only when something goes wrong.
"""

import getpass
import platform
import secrets
import time
from . import browser, store, ui
from .relay import Relay, RelayError
from .store import LOG

GIVE_UP_SECONDS = 330  # a little longer than the relay keeps the request


def device_name():
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return (platform.node() + " " + user).strip() or "unnamed computer"


def pair(document="", relay=None, open_page=browser.open_page, sleep=time.sleep, clock=time.monotonic):
    """Pair and save the token; whether the page opened the first chat, or None when pairing did not finish."""
    relay = relay or Relay()
    device_id = secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32)
    try:
        status, started = relay.request("POST", "/api/v1/connect/start", {
            "device_id": device_id, "secret": secret, "device_name": device_name(),
            "document": document})
    except RelayError as error:
        store.write_status("not_connected")
        ui.tell("Chat2Rhino cannot reach HdM Chat:\n{0}".format(error))
        return None
    if status != 200 or not started.get("connect_url"):
        store.write_status("not_connected")
        ui.tell("Chat2Rhino cannot start connecting (answer {0}). Try again later.".format(status))
        return None
    store.write_status("pairing")
    LOG.info("Pairing started")
    open_page(started["connect_url"])

    interval = float(started.get("poll_seconds") or 2)
    deadline = clock() + GIVE_UP_SECONDS
    while clock() < deadline:
        if store.stop_requested():
            store.write_status("not_connected")
            return None
        sleep(interval)
        try:
            status, polled = relay.request("POST", "/api/v1/connect/poll",
                                           {"device_id": device_id, "secret": secret})
        except RelayError as error:
            LOG.warning("Poll failed: %s", error)
            continue
        if status == 200 and polled.get("status") == "approved":
            store.save_token(polled["user_key"], polled["connector_token"], device_id)
            LOG.info("Paired as %s", polled["user_key"])
            return bool(polled.get("chat_opened"))
        if status == 403 and polled.get("status") == "not_in_group":
            LOG.info("%s is not in the group %s", polled.get("user"), polled.get("group"))
            store.write_status("not_in_group")
            ui.tell(ui.COMING_SOON)
            return None
        if status == 429:
            interval += 1
        elif status != 200:
            break
    store.write_status("not_connected")
    ui.tell("Connecting Rhino to HdM Chat did not finish in time. Click Chat2Rhino to try "
            "again.")
    return None
