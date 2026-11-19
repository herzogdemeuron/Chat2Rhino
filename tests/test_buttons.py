# -*- coding: utf-8 -*-
"""The Rhino button scripts run only inside Rhino; check what can be checked here."""

import ast
import builtins
import os
import unittest

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "toolbar", "Chat2Rhino.button")
SCRIPTS = ("Chat2Rhino_lmb.py", "Chat2Rhino_rmb.py", "chat2rhino_button.py")


def _defined(tree):
    names = set(dir(builtins)) | {"reload", "__file__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


class ButtonScriptTests(unittest.TestCase):
    def test_every_called_name_is_defined(self):
        for name in SCRIPTS:
            with open(os.path.join(FOLDER, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            defined = _defined(tree)
            called = {node.func.id for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            self.assertEqual(set(), called - defined, name)

    def test_calls_into_the_shared_module_exist(self):
        with open(os.path.join(FOLDER, "chat2rhino_button.py"), encoding="utf-8") as handle:
            shared = _defined(ast.parse(handle.read()))
        for name in ("Chat2Rhino_lmb.py", "Chat2Rhino_rmb.py"):
            with open(os.path.join(FOLDER, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name) and node.value.id == "common"}
            self.assertEqual(set(), used - shared, name)


if __name__ == "__main__":
    unittest.main()
