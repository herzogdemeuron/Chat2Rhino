# Chat2Rhino

Use Rhino from HdM Chat: the Chat2Rhino model works in the Rhino you have
open, through McNeel's [RhinoAI](https://github.com/mcneel/RhinoAI) plug-in.

Who may use it is decided by the HdM Chat user group `Chat2Rhino`.

## How it works

```
HdM Chat (model Chat2Rhino)
   |  tool calls
   v
Chat2Rhino relay (HdM Chat cluster, chatbot.herzogdemeuron.com/chat2rhino)
   ^  the connector asks for jobs over HTTPS (outgoing only)
   |
Chat2Rhino connector (this repository, on your computer, outside Rhino)
   |  RhinoAI's MCP server on localhost (started with MCPStart)
   v
Rhino
```

- The button starts RhinoAI's server in Rhino (`MCPStart`) and the connector
  in the background.
- The first time, the connector opens a page in your browser: sign in with
  Microsoft and click Connect. The connection is kept (encrypted for your
  Windows account) until you remove it.
- Each click opens a new HdM Chat chat with the Chat2Rhino model for that
  Rhino ("<document> is connected to this chat.", press Enter). That chat
  works in that Rhino only; with several Rhinos open, click Chat2Rhino in
  each for a chat of its own.
- The connector checks every job against your access level before it reaches
  Rhino, and stops by itself two minutes after the last Rhino closes.

## Install (test group)

Needs Rhino 8. No admin rights. RhinoAI (Yak package `Rhino-MCP-Platform`
0.4.0, the version Chat2Rhino is tested with) is installed by the first click,
once HdM Chat has accepted the computer, so only members of the group get it.

1. Put this repository at `C:\HdM-DT\RhinoToolbarExtensions\Chat2Rhino`
   (clone it, or unpack a download there).
2. In Rhino: Options > Aliases, add
   - `Chat2Rhino`: `! _-RunPythonScript "C:\HdM-DT\RhinoToolbarExtensions\Chat2Rhino\toolbar\Chat2Rhino.button\Chat2Rhino_lmb.py"`
   - `Chat2RhinoOff`: `! _-RunPythonScript "C:\HdM-DT\RhinoToolbarExtensions\Chat2Rhino\toolbar\Chat2Rhino.button\Chat2Rhino_rmb.py"`
3. Type `Chat2Rhino` in Rhino: the first time a page connects this computer
   to your HdM Chat account (sign in, Connect), then a chat opens.

If Rhino says no Python was found, run any Python script in Rhino 8 once
(`ScriptEditor`), so that Rhino installs its Python, then try again.

## Use

- `Chat2Rhino` (left click): open a new chat for this Rhino (connecting the
  computer first, the first time). Press Enter on the line it brings; that
  chat works in this Rhino.
- `Chat2RhinoOff` (right click): disconnect this Rhino. RhinoAI's server in
  it stops, so HdM Chat cannot reach it; its chats come back after a left
  click. Other Rhinos stay connected.
- Connect again (another account, or after a problem):
  `python -m chat2rhino forget` in this folder, then `Chat2Rhino`.

Files: `%LOCALAPPDATA%\HdM\Chat2Rhino` (`token.json`, `status.json`,
`connector.log`).

## Development

Standard library only, Python 3.9 or newer (Rhino 8's own Python is 3.9).

```
python -m unittest discover -s tests -t .
```

`CHAT2RHINO_RELAY_URL` and `CHAT2RHINO_STATE_DIR` point the connector at
another relay and state folder. The relay is HdM's Chat2Rhino relay;
`chat2rhino/access.py` keeps a copy of its Rhino access table.

## Licence

MIT, see [LICENSE](LICENSE). RhinoAI is McNeel's
([github.com/mcneel/RhinoAI](https://github.com/mcneel/RhinoAI), MIT); this
repository only talks to its MCP server.
