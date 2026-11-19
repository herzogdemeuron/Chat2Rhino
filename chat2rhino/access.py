# -*- coding: utf-8 -*-
"""The second access check, on this computer.

The relay resolves the user's access level and stamps it on every job
(``access``); it already refuses a tool above that level. The
connector checks again against its own table before anything reaches Rhino,
so a relay that sends more than it should still cannot reach a tool the
level does not allow. A job without a stamp counts as Reading; a tool not in
the table is refused.

``TOOL_LEVELS`` is the same table the Chat2Rhino relay uses; keep the two the
same.
"""

READING, EDITING, MANAGING = "reading", "editing", "managing"
_RANK = {READING: 1, "drafting": 2, EDITING: 3, MANAGING: 4}
ROUTE_PREFIX = "/rhino_mcp/"

# Every RhinoAI tool Chat2Rhino offers, with the lowest level that may call it.
TOOL_LEVELS = {
    "get_context": READING,
    "get_selection": READING,
    "get_viewport_image": READING,
    "list_objects": READING,
    "get_commands": READING,
    "set_camera": READING,
    "set_selection": READING,
    "zoom_to_layer": READING,
    "zoom_to_object": READING,
    "g1_get_canvas_graph": READING,
    "g1_describe_component": READING,
    "g1_search_components": READING,
    "run_python": EDITING,
    "set_layer_material": EDITING,
    "g1_start": EDITING,
    "g1_place_component": EDITING,
    "g1_place_slider": EDITING,
    "g1_connect": EDITING,
    "g1_connect_many": EDITING,
    "g1_apply_graph": EDITING,
    "g1_delete_component": EDITING,
    "g1_solve_graph": EDITING,
    "run_command": MANAGING,
    "run_csharp": MANAGING,
    "open_doc": MANAGING,
    "save_doc": MANAGING,
    "close_doc": MANAGING,
    "g1_clear_canvas": MANAGING,
    # Served by the connector itself (sessions.py).
    "rhino_sessions": READING,
    "rhino_bind": READING,
}


def tool_of(route):
    """``/rhino_mcp/<tool>/`` -> ``<tool>``, or "" for anything else."""
    route = str(route or "")
    if not route.startswith(ROUTE_PREFIX):
        return ""
    tool = route[len(ROUTE_PREFIX):].strip("/")
    return "" if "/" in tool else tool


def denial(request):
    """None when the job may run, else a failed job result."""
    request = request if isinstance(request, dict) else {}
    route = request.get("target_route", "")
    if str(request.get("software") or "").lower() != "rhino":
        return {"ok": False, "error": "unsupported software: {0}".format(request.get("software"))}
    required = TOOL_LEVELS.get(tool_of(route))
    level = str(request.get("access") or "").lower()
    level = level if level in _RANK else READING
    if required is not None and _RANK[level] >= _RANK[required]:
        return None
    return {"ok": False, "error": "permission_denied",
            "response": {"status": "permission_denied", "route": route,
                         "role": level, "required_min_role": required}}
