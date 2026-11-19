# -*- coding: utf-8 -*-
"""Problems shown to the user: a Windows message box (the connector has no window)."""

from .store import LOG

TITLE = "Chat2Rhino"
# What someone outside the test group is told (the page and the connector say the same).
COMING_SOON = "Chat2Rhino is coming soon."
_MB_ICONWARNING = 0x30
_MB_SETFOREGROUND = 0x10000
_MB_TOPMOST = 0x40000


def _show(text):
    LOG.info("Message: %s", text.replace("\n", " "))
    try:
        import ctypes

        flags = _MB_ICONWARNING | _MB_SETFOREGROUND | _MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)
    except (AttributeError, OSError):
        pass  # not Windows: the log has it


def tell(text):
    """Show *text* and wait for OK: every message comes right before the connector exits."""
    _show(text)
