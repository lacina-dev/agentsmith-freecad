import FreeCADGui as Gui


class AgentSmithWorkbench(Gui.Workbench):
    # NOTE: FreeCAD executes InitGui.py inside a function scope, so names defined at
    # the top level of this file are NOT visible from this class body. Everything the
    # class body needs must be computed right here (literals or inline imports).
    MenuText = "AgentSmith"
    ToolTip = "AI modeling agent: supervised bridge and chat panel for the active document"
    try:
        import os
        import FreeCAD
        Icon = ""
        _mod_root = os.path.join(FreeCAD.getUserAppDataDir(), "Mod")
        _direct = os.path.join(_mod_root, "AgentSmith", "resources", "AgentSmith.svg")
        if os.path.isfile(_direct):
            Icon = _direct
        elif os.path.isdir(_mod_root):
            for _entry in os.listdir(_mod_root):
                _candidate = os.path.join(_mod_root, _entry, "resources", "AgentSmith.svg")
                if os.path.isfile(_candidate):
                    Icon = _candidate
                    break
        del os, FreeCAD, _mod_root, _direct
    except Exception:
        Icon = ""

    def Initialize(self):
        import BridgeGui
        BridgeGui.register_commands()
        self.appendToolbar("AgentSmith", ["AgentSmith_ShowPanel"])
        self.appendMenu("AgentSmith", ["AgentSmith_ShowPanel"])

    def Activated(self):
        import BridgeGui
        BridgeGui.show_panel()

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(AgentSmithWorkbench())
