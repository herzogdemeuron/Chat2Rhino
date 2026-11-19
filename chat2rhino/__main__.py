# -*- coding: utf-8 -*-
"""``python -m chat2rhino [start|stop|status|forget]``.

``start`` (what the Rhino button runs) pairs this computer if it is not
paired yet, then serves jobs until Rhino closes or ``stop`` is asked for.
Only one connector runs per Windows user; a second ``start`` exits at once.
"""

import argparse
import json
import os
import sys
import time

from . import __version__, store
from .connector import JOB_GRACE_SECONDS
from .store import LOG


def _single_instance():
    """True when no other connector of this user is running."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateMutexW(None, False, "Local\\Chat2Rhino-connector")
        if handle and ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _single_instance.handle = handle  # held for the life of the process
            return True
        if handle:
            kernel32.CloseHandle(handle)
        return False
    import fcntl

    _single_instance.handle = open(store.path("lock"), "w")
    try:
        fcntl.flock(_single_instance.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def start():
    store.setup_logging()
    # A connector that was just asked to stop may still be finishing a job:
    # wait for it rather than leaving nothing running.
    deadline = time.time() + JOB_GRACE_SECONDS + 15
    while not _single_instance():
        if not store.stop_requested() or time.time() > deadline:
            LOG.info("A connector is already running")
            return 0
        time.sleep(2)
    store.clear_stop()
    LOG.info("Chat2Rhino %s starting (Python %s)", __version__, sys.version.split()[0])
    from . import pairing
    from .connector import Connector

    for _attempt in range(2):  # a token the relay refuses: pair again once
        clicks = store.read_json("pending.json").get("clicks") or [{}]
        click = clicks[-1]  # the click that started this
        page_opened_chat = False
        if not store.load_token():
            page_opened_chat = pairing.pair(click.get("document", ""))
        paired = store.load_token()
        if not paired:
            return 1  # pairing wrote the status and told the user
        connector = Connector(*paired)
        if page_opened_chat:
            connector.bindings.chat_opened(click)
        code = connector.run()
        if not connector.revoked:
            return code
        store.forget_token()
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="chat2rhino")
    parser.add_argument("command", nargs="?", default="start",
                        choices=("start", "stop", "status", "forget"))
    args = parser.parse_args(argv)
    if args.command == "start":
        return start()
    if args.command == "stop":
        store.request_stop()
        return 0
    if args.command == "forget":
        store.forget_token()
        return 0
    print(json.dumps(store.read_status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
