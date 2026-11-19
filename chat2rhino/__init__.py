# -*- coding: utf-8 -*-
"""Chat2Rhino: connect Rhino to HdM Chat.

A small connector that runs next to Rhino, outside it. It pairs the computer
with the user's HdM Chat account once (Microsoft sign-in in the browser),
then polls the Chat2Rhino relay for the jobs the user's chats send to Rhino
and runs them through McNeel's RhinoAI MCP server on this computer.

Standard library only; runs on Rhino 8's own Python 3.9 or any Python 3.9+.
"""

__version__ = "0.1.0"
