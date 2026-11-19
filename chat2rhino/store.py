# -*- coding: utf-8 -*-
"""Files in the state folder (``%LOCALAPPDATA%\\HdM\\Chat2Rhino``).

- ``token.json``: the connector token, encrypted for this Windows user with
  DPAPI (only this user on this computer can read it).
- ``status.json``: what the connector is doing, for the Rhino button.
- ``stop``: asks the connector to stop (``python -m chat2rhino stop``).
- ``pending.json``: written by the button's left click: the Rhino that waits
  for a chat (``sessions``), and whether to open one.
- ``bindings.json``: which Rhino each chat works in (``sessions``).
- ``connector.log``: the connector's log.
"""

import base64
import ctypes
import json
import logging
import logging.handlers
import os
import threading
import time

from . import settings

LOG = logging.getLogger("chat2rhino")


def path(name):
    """A file in the state folder (created on first use)."""
    os.makedirs(settings.STATE_DIR, exist_ok=True)
    return os.path.join(settings.STATE_DIR, name)


def remove(name):
    try:
        os.remove(path(name))
    except FileNotFoundError:
        pass


def write_json(name, payload):
    tmp = "{0}.{1}.tmp".format(path(name), threading.get_ident())  # threads write status.json too
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    for attempt in range(5):  # on Windows a reader (the button, a virus scanner) may hold the file
        try:
            os.replace(tmp, path(name))
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1)


def read_json(name):
    try:
        with open(path(name), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def setup_logging():
    handler = logging.handlers.RotatingFileHandler(
        path("connector.log"), maxBytes=1000000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)


# -- DPAPI (Windows); elsewhere the token is kept as it is, for tests --

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.c_void_p)]


def _dpapi(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    blob_out = _Blob()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # No description, entropy or prompt; CRYPTPROTECT_UI_FORBIDDEN.
    if not call(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _protect(text):
    raw = text.encode("utf-8")
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")


def _unprotect(stored):
    kind, _, data = str(stored).partition(":")
    raw = base64.b64decode(data)
    if kind == "dpapi":
        raw = _dpapi(raw, False)
    elif kind != "plain":
        raise ValueError("unknown token protection")
    return raw.decode("utf-8")


def save_token(user_key, token, device_id):
    write_json("token.json", {"user_key": user_key, "device_id": device_id,
                               "token": _protect(token), "paired_at": int(time.time())})


def load_token():
    """``(user_key, token, device_id)``, or None when not paired."""
    data = read_json("token.json")
    if not data.get("token"):
        return None
    try:
        return data.get("user_key", ""), _unprotect(data["token"]), data.get("device_id", "")
    except (OSError, ValueError):
        LOG.warning("Saved token cannot be read; pairing again")
        return None


def forget_token():
    remove("token.json")


# -- status and stop request --

def write_status(state, **fields):
    try:
        write_json("status.json", dict(fields, state=state, pid=os.getpid(), updated_at=int(time.time())))
    except OSError as error:
        LOG.warning("Writing the status (%s) failed: %s", state, error)


def read_status():
    return read_json("status.json")


def request_stop():
    with open(path("stop"), "w") as handle:
        handle.write(str(int(time.time())))


def stop_requested():
    return os.path.exists(path("stop"))


def clear_stop():
    remove("stop")
