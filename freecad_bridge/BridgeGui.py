import json
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from collections import deque

import FreeCAD as App
import FreeCADGui as Gui
import Part
from pivy import coin
from PySide import QtCore, QtGui, QtNetwork

try:
    from PySide import QtWidgets
except ImportError:  # FreeCAD versions exposing widgets through QtGui
    QtWidgets = QtGui


BRIDGE_VERSION = "0.17.0"
DEFAULT_PORT = 18421
DISCOVERY_FILE = "/tmp/freecad-agentsmith-bridge.json"
EVENT_FILE = "/tmp/freecad-agentsmith-events.jsonl"
# Hard wall-clock limit for a single live-editing task. The watchdog terminates
# the backend once this is exceeded. It is also surfaced to the backend so it can
# budget its time and act decisively instead of investigating until it is killed.
LIVE_EDIT_BUDGET_SECONDS = 480
# Directory holding the extensible modeling-harness playbooks (registry.json + *.md).
HARNESS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
# Camera fields captured/restored so a task returns the user's view exactly as it was.
_CAMERA_FIELDS = ("position", "orientation", "focalDistance", "height", "heightAngle")
# Orientations that can be captured as reference snapshots, in a stable order.
CAPTURE_ORIENTATIONS = ("top", "bottom", "front", "rear", "left", "right")
_panel = None


class CornerCutProxy:
    """Parametric feature that truncates the eight vertices of a box-like solid."""

    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        base = obj.Base
        if base is None or base.Shape.isNull():
            obj.Shape = Part.Shape()
            return
        size = obj.Size.Value
        bounds = base.Shape.BoundBox
        shortest = min(bounds.XLength, bounds.YLength, bounds.ZLength)
        if size <= 0 or size * 2 >= shortest:
            raise ValueError("Corner cut must be greater than 0 and less than half the shortest side")

        result = base.Shape
        for x_high in (False, True):
            for y_high in (False, True):
                for z_high in (False, True):
                    x = bounds.XMax if x_high else bounds.XMin
                    y = bounds.YMax if y_high else bounds.YMin
                    z = bounds.ZMax if z_high else bounds.ZMin
                    dx = -size if x_high else size
                    dy = -size if y_high else size
                    dz = -size if z_high else size
                    corner = App.Vector(x, y, z)
                    px = App.Vector(x + dx, y, z)
                    py = App.Vector(x, y + dy, z)
                    pz = App.Vector(x, y, z + dz)
                    faces = [
                        Part.Face(Part.makePolygon([corner, px, py, corner])),
                        Part.Face(Part.makePolygon([corner, py, pz, corner])),
                        Part.Face(Part.makePolygon([corner, pz, px, corner])),
                        Part.Face(Part.makePolygon([px, pz, py, px])),
                    ]
                    cutter = Part.makeSolid(Part.makeShell(faces))
                    result = result.cut(cutter)
        obj.Shape = result

    def onDocumentRestored(self, obj):
        obj.Proxy = self


def create_corner_cut(doc, base, name="CornerCut", label="Parametric corner cuts"):
    obj = doc.addObject("PartDesign::FeaturePython", name)
    obj.Label = label
    obj.addProperty("App::PropertyLink", "Base", "Corner cut", "Source solid")
    obj.addProperty("App::PropertyLength", "Size", "Corner cut", "Distance measured from each vertex along its three edges")
    obj.Base = base
    obj.Size = 10.0
    CornerCutProxy(obj)
    return obj


def _json_value(value):
    """Convert common FreeCAD values to JSON-safe data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "Value") and hasattr(value, "Unit"):
        return {"value": value.Value, "unit": str(value.Unit), "text": str(value)}
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        return [value.x, value.y, value.z]
    if hasattr(value, "Name") and hasattr(value, "Document"):
        return value.Name
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    try:
        return str(value)
    except Exception:
        return "<unserializable>"


def _document(name=None):
    doc = App.getDocument(name) if name else App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active FreeCAD document")
    return doc


def _optional_document(name):
    try:
        return App.getDocument(name)
    except Exception:
        return None


def _object(doc, name):
    obj = doc.getObject(name)
    if obj is None:
        raise KeyError("Object not found: %s" % name)
    return obj


def _object_summary(obj, include_properties=False):
    data = {
        "name": obj.Name,
        "label": obj.Label,
        "type_id": obj.TypeId,
        "properties": {},
    }
    if hasattr(obj, "Shape"):
        shape = obj.Shape
        data["shape"] = {
            "is_null": shape.isNull(),
            "is_valid": False if shape.isNull() else shape.isValid(),
            "solids": len(shape.Solids),
            "volume": shape.Volume,
            "bounds": None if shape.isNull() else {
                "x": shape.BoundBox.XLength,
                "y": shape.BoundBox.YLength,
                "z": shape.BoundBox.ZLength,
            },
        }
    if include_properties:
        expressions = dict(getattr(obj, "ExpressionEngine", []))
        for name in obj.PropertiesList:
            try:
                data["properties"][name] = {
                    "type": obj.getTypeIdOfProperty(name),
                    "group": obj.getGroupOfProperty(name),
                    "value": _json_value(getattr(obj, name)),
                    "expression": expressions.get(name),
                }
            except Exception as exc:
                data["properties"][name] = {"error": str(exc)}
    return data


def _validate_document(doc):
    report = {"document": doc.Name, "ok": True, "objects": [], "errors": [], "warnings": []}
    try:
        doc.recompute()
    except Exception as exc:
        report["errors"].append("Recompute failed: %s" % exc)
    for obj in doc.Objects:
        state = [str(item) for item in getattr(obj, "State", [])]
        item = {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId, "state": state}
        if state and state != ["Up-to-date"]:
            report["warnings"].append({"object": obj.Name, "state": state})
        if hasattr(obj, "Shape"):
            shape = obj.Shape
            item["shape_null"] = shape.isNull()
            item["shape_valid"] = False if shape.isNull() else shape.isValid()
            item["solids"] = len(shape.Solids)
            if shape.isNull() and getattr(obj, "Visibility", True):
                report["warnings"].append({"object": obj.Name, "problem": "null shape"})
            elif not shape.isNull() and not shape.isValid():
                report["errors"].append({"object": obj.Name, "problem": "invalid shape"})
        report["objects"].append(item)
    report["ok"] = not report["errors"]
    return report


def _document_fingerprint(doc):
    """Stable-enough model fingerprint used to prove that a task changed the live document."""
    objects = []
    for obj in doc.Objects:
        item = {
            "name": obj.Name,
            "type": obj.TypeId,
            "label": obj.Label,
            "expressions": list(getattr(obj, "ExpressionEngine", [])),
        }
        if hasattr(obj, "Shape") and not obj.Shape.isNull():
            item["shape"] = {
                "hash": int(obj.Shape.hashCode()),
                "volume": round(obj.Shape.Volume, 9),
                "faces": len(obj.Shape.Faces),
                "edges": len(obj.Shape.Edges),
            }
        if obj.TypeId == "Spreadsheet::Sheet" and hasattr(obj, "getNonEmptyCells"):
            item["cells"] = {cell: str(obj.get(cell)) for cell in obj.getNonEmptyCells()}
        objects.append(item)
    payload = json.dumps(objects, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fcstd_archive_valid(path):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 100:
            return False
        with zipfile.ZipFile(path, "r") as archive:
            return "Document.xml" in archive.namelist() and archive.testzip() is None
    except Exception:
        return False


def _atomic_write_bytes(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = os.path.join(directory, ".%s.bridge-restore-%s.tmp" % (os.path.basename(path), secrets.token_hex(4)))
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _rss_bytes():
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


def _camera_snapshot(view):
    """Capture the camera node fields so the exact view can be restored later."""
    try:
        node = view.getCameraNode()
    except Exception:
        return None
    fields = {}
    for name in _CAMERA_FIELDS:
        field = getattr(node, name, None)
        if field is None:
            continue
        try:
            value = field.getValue()
            fields[name] = value.getValue() if hasattr(value, "getValue") else value
        except Exception:
            pass
    return {"fields": fields} if fields else None


def _camera_apply(view, state):
    """Restore a camera snapshot produced by _camera_snapshot."""
    if not state:
        return
    try:
        node = view.getCameraNode()
    except Exception:
        return
    for name, value in state.get("fields", {}).items():
        try:
            getattr(node, name).setValue(value)
        except Exception:
            pass


class DocumentObserver:
    def __init__(self, callback):
        self.callback = callback

    def slotActivateDocument(self, doc):
        self.callback("document_activated", {"document": doc.Name})

    def slotCreatedObject(self, obj):
        self.callback("object_created", {"document": obj.Document.Name, "object": obj.Name, "type_id": obj.TypeId})

    def slotDeletedObject(self, obj):
        self.callback("object_deleted", {"object": getattr(obj, "Name", "")})

    def slotChangedObject(self, obj, prop):
        if prop not in ("Shape", "Visibility"):
            self.callback("object_changed", {"document": obj.Document.Name, "object": obj.Name, "property": prop})


class BridgeServer(QtCore.QObject):
    log_message = QtCore.Signal(str)
    state_changed = QtCore.Signal(bool, int)
    safety_violation = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QtNetwork.QTcpServer(self)
        self.server.newConnection.connect(self._accept_connections)
        self.clients = set()
        self.buffers = {}
        self.token = secrets.token_urlsafe(24)
        self.allow_python = False
        self.access_level = "edit"
        self.protected_documents = {}
        self.supervised_metrics = None
        self.events = deque(maxlen=2000)
        self.event_sequence = 0
        self.observer = DocumentObserver(self._record_event)

    def start(self, port=DEFAULT_PORT):
        if self.server.isListening():
            return self.server.serverPort()
        address = QtNetwork.QHostAddress(QtNetwork.QHostAddress.LocalHost)
        if not self.server.listen(address, port):
            if not self.server.listen(address, 0):
                raise RuntimeError(self.server.errorString())
        actual_port = self.server.serverPort()
        App.addDocumentObserver(self.observer)
        try:
            if os.path.exists(EVENT_FILE) and os.path.getsize(EVENT_FILE) > 5 * 1024 * 1024:
                os.replace(EVENT_FILE, EVENT_FILE + ".old")
        except Exception:
            pass
        self._write_discovery(actual_port)
        self.log_message.emit("Listening on 127.0.0.1:%d" % actual_port)
        self.state_changed.emit(True, actual_port)
        return actual_port

    def stop(self):
        for client in list(self.clients):
            client.disconnectFromHost()
        self.clients.clear()
        self.buffers.clear()
        self.server.close()
        try:
            App.removeDocumentObserver(self.observer)
        except Exception:
            pass
        try:
            with open(DISCOVERY_FILE, "r", encoding="utf-8") as handle:
                owner = json.load(handle)
            if owner.get("pid") == os.getpid() and owner.get("token") == self.token:
                os.unlink(DISCOVERY_FILE)
        except (OSError, ValueError, TypeError):
            pass
        self.log_message.emit("Bridge stopped")
        self.state_changed.emit(False, 0)

    def _write_discovery(self, port):
        payload = {
            "version": BRIDGE_VERSION,
            "pid": os.getpid(),
            "host": "127.0.0.1",
            "port": port,
            "token": self.token,
        }
        temp = DISCOVERY_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.chmod(temp, 0o600)
        os.replace(temp, DISCOVERY_FILE)

    def ensure_discovery(self):
        if not self.server.isListening():
            raise RuntimeError("Bridge server is not listening")
        valid = False
        try:
            with open(DISCOVERY_FILE, "r", encoding="utf-8") as handle:
                current = json.load(handle)
            valid = (current.get("pid") == os.getpid() and current.get("token") == self.token
                     and int(current.get("port", 0)) == self.server.serverPort())
        except Exception:
            valid = False
        if not valid:
            self._write_discovery(self.server.serverPort())
            self.log_message.emit("Discovery lease repaired")
        return DISCOVERY_FILE

    def _record_event(self, event_type, data):
        self.event_sequence += 1
        event = {"seq": self.event_sequence, "time": time.time(), "type": event_type, "data": data}
        self.events.append(event)
        metrics = self.supervised_metrics
        if metrics is not None and event_type in ("object_created", "object_deleted", "object_changed"):
            metrics["mutation_events"] = metrics.get("mutation_events", 0) + 1
            name = data.get("object", "")
            counts = metrics.setdefault("object_events", {})
            counts[name] = counts.get(name, 0) + 1
            # Spreadsheets are the model's central parameter store: every cell write and
            # every dependent recompute touches them, so a legitimate parameter-rich task
            # easily exceeds the generic cap (seen live: a wall-hook build was killed at
            # 180 Parameters events while working correctly). Give them more headroom;
            # keep the tight cap for geometry objects, where 180 edits IS pathological.
            types = metrics.setdefault("object_type_cache", {})
            if name and name not in types:
                type_id = ""
                try:
                    for lookup_doc in App.listDocuments().values():
                        obj = lookup_doc.getObject(name)
                        if obj is not None:
                            type_id = str(getattr(obj, "TypeId", ""))
                            break
                except Exception:
                    type_id = ""
                types[name] = type_id
            per_object_cap = 600 if types.get(name, "").startswith("Spreadsheet::") else 180
            if metrics["mutation_events"] > 3000:
                self.safety_violation.emit("Task exceeded 3000 live document mutation events")
            elif name and counts[name] > per_object_cap:
                self.safety_violation.emit("Task repeatedly changed %s more than %d times" % (name, per_object_cap))
        try:
            with open(EVENT_FILE, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _accept_connections(self):
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            self.clients.add(client)
            self.buffers[client] = b""
            client.readyRead.connect(lambda c=client: self._read(c))
            client.disconnected.connect(lambda c=client: self._drop(c))
            self.log_message.emit("Client connected")

    def _drop(self, client):
        self.clients.discard(client)
        self.buffers.pop(client, None)
        client.deleteLater()

    def _read(self, client):
        self.buffers[client] += bytes(client.readAll())
        while b"\n" in self.buffers[client]:
            line, self.buffers[client] = self.buffers[client].split(b"\n", 1)
            if not line.strip():
                continue
            try:
                request = json.loads(line.decode("utf-8"))
                response = self._dispatch(request)
            except Exception as exc:
                response = {
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                }
            client.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            client.flush()

    def _dispatch(self, request):
        if request.get("token") != self.token:
            raise PermissionError("Invalid bridge token")
        command = request.get("command")
        args = request.get("args") or {}
        handlers = {
            "ping": self._ping,
            "documents": self._documents,
            "document_info": self._document_info,
            "object_info": self._object_info,
            "model_digest": self._model_digest,
            "mass_properties": self._mass_properties,
            "measure": self._measure,
            "check_solid": self._check_solid,
            "interference_check": self._interference_check,
            "view_state": self._view_state,
            "view_restore": self._view_restore,
            "selection": self._selection,
            "selection_set": self._selection_set,
            "spreadsheet_set": self._spreadsheet_set,
            "spreadsheet_info": self._spreadsheet_info,
            "sketch_info": self._sketch_info,
            "sketch_set_datum": self._sketch_set_datum,
            "set_property": self._set_property,
            "set_expression": self._set_expression,
            "set_visibility": self._set_visibility,
            "create_object": self._create_object,
            "remove_object": self._remove_object,
            "recompute": self._recompute,
            "save": self._save,
            "checkpoint": self._checkpoint,
            "list_checkpoints": self._list_checkpoints,
            "open_checkpoint": self._open_checkpoint,
            "undo": self._undo,
            "redo": self._redo,
            "history": self._history,
            "validate": self._validate,
            "batch": self._batch,
            "events": self._events,
            "capabilities": self._capabilities,
            "export": self._export,
            "fit_view": self._fit_view,
            "set_view": self._set_view,
            "screenshot": self._screenshot,
            "execute_python": self._execute_python,
            "cross_section": self._section,
            "print_readiness": self._print_readiness,
        }
        if command not in handlers:
            raise ValueError("Unknown command: %s" % command)
        read_commands = {"ping", "documents", "document_info", "object_info", "model_digest", "mass_properties", "measure", "check_solid", "interference_check", "cross_section", "view_state", "selection", "history", "validate", "events", "capabilities", "list_checkpoints", "spreadsheet_info", "sketch_info", "print_readiness"}
        if self.access_level == "read" and command not in read_commands:
            raise PermissionError("Bridge is in read-only mode")
        transactional_commands = {"spreadsheet_set", "sketch_set_datum", "set_property", "set_expression", "create_object", "remove_object", "execute_python"}
        transaction_doc = None
        auto_transaction = False
        if command in transactional_commands:
            transaction_doc = _document(args.get("document"))
            auto_transaction = not bool(transaction_doc.HasPendingTransaction)
            if auto_transaction:
                transaction_doc.openTransaction(args.get("transaction_label", "AgentSmith: %s" % command))
        try:
            result = handlers[command](args)
            if auto_transaction:
                transaction_doc.recompute()
                validation = _validate_document(transaction_doc)
                if args.get("require_valid", True) and not validation["ok"]:
                    raise RuntimeError("Validation failed: %s" % validation["errors"])
                transaction_doc.commitTransaction()
        except Exception:
            if auto_transaction and transaction_doc.HasPendingTransaction:
                transaction_doc.abortTransaction()
                transaction_doc.recompute()
            raise
        self.log_message.emit("OK: %s" % command)
        return {"ok": True, "result": result}

    def _ping(self, args):
        return {"bridge_version": BRIDGE_VERSION, "freecad_version": App.Version(), "python_enabled": self.allow_python, "access_level": self.access_level}

    def _capabilities(self, args):
        return {
            "protocol": BRIDGE_VERSION,
            "access_level": self.access_level,
            "commands": ["ping", "documents", "document_info", "object_info", "model_digest", "mass_properties", "measure", "check_solid", "interference_check", "cross_section", "view_state", "view_restore", "selection", "selection_set", "spreadsheet_set", "spreadsheet_info", "sketch_info", "sketch_set_datum", "set_property", "set_expression", "set_visibility", "create_object", "remove_object", "recompute", "save", "checkpoint", "list_checkpoints", "open_checkpoint", "undo", "redo", "history", "validate", "batch", "events", "export", "fit_view", "set_view", "screenshot", "execute_python", "print_readiness"],
            "features": {"transactions": True, "checkpoints": True, "event_journal": True, "chat_panel": True, "model_digest": True, "camera_restore": True, "measuring": True, "cross_section": True, "reference_inputs": True, "task_budget": True, "print_readiness": True, "claude_vision_via_read": True},
        }

    def _documents(self, args):
        return {"active": App.ActiveDocument.Name if App.ActiveDocument else None, "documents": list(App.listDocuments().keys())}

    def _document_info(self, args):
        doc = _document(args.get("document"))
        return {
            "name": doc.Name,
            "label": doc.Label,
            "file_name": doc.FileName,
            "modified": getattr(doc, "Modified", None),
            "objects": [_object_summary(obj) for obj in doc.Objects],
        }

    def _object_info(self, args):
        doc = _document(args.get("document"))
        return _object_summary(_object(doc, args["object"]), True)

    def _model_digest(self, args):
        """Standardized, dimensional snapshot of the whole model.

        This is the canonical way for a task to learn how the model looks: every object
        with its bounding box + size, volume, area and topology counts, placement, plus
        all spreadsheet aliases/values and sketch constraints/datums.
        """
        doc = _document(args.get("document"))
        include_props = bool(args.get("properties", False))
        overall = {"min": [None, None, None], "max": [None, None, None]}

        def _grow(box):
            mins = [box.XMin, box.YMin, box.ZMin]
            maxs = [box.XMax, box.YMax, box.ZMax]
            for i in range(3):
                overall["min"][i] = mins[i] if overall["min"][i] is None else min(overall["min"][i], mins[i])
                overall["max"][i] = maxs[i] if overall["max"][i] is None else max(overall["max"][i], maxs[i])

        objects, spreadsheets, sketches = [], {}, {}
        for obj in doc.Objects:
            entry = {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId,
                     "visible": bool(getattr(obj, "Visibility", False))}
            placement = getattr(obj, "Placement", None)
            if placement is not None:
                try:
                    entry["placement"] = {
                        "position": [placement.Base.x, placement.Base.y, placement.Base.z],
                        "yaw_pitch_roll": list(placement.Rotation.toEuler()),
                    }
                except Exception:
                    pass
            shape = getattr(obj, "Shape", None)
            if shape is not None and not shape.isNull():
                box = shape.BoundBox
                entry["bounding_box"] = {
                    "min": [box.XMin, box.YMin, box.ZMin],
                    "max": [box.XMax, box.YMax, box.ZMax],
                    "size": [box.XLength, box.YLength, box.ZLength],
                }
                entry["volume"] = shape.Volume
                entry["area"] = shape.Area
                entry["counts"] = {"solids": len(shape.Solids), "faces": len(shape.Faces),
                                   "edges": len(shape.Edges), "vertexes": len(shape.Vertexes)}
                if getattr(obj, "Visibility", False):
                    _grow(box)
            if include_props:
                entry["properties"] = _object_summary(obj, True).get("properties", {})
            objects.append(entry)

            if obj.TypeId == "Spreadsheet::Sheet" and hasattr(obj, "getNonEmptyCells"):
                cells = {}
                for cell in obj.getNonEmptyCells():
                    info = {"value": _json_value(obj.get(cell))}
                    try:
                        alias = obj.getAlias(cell)
                        if alias:
                            info["alias"] = alias
                    except Exception:
                        pass
                    try:
                        content = obj.getContents(cell)
                        if content:
                            info["content"] = content
                    except Exception:
                        pass
                    cells[cell] = info
                spreadsheets[obj.Name] = cells

            if obj.TypeId.startswith("Sketcher::"):
                sketches[obj.Name] = {
                    "fully_constrained": bool(getattr(obj, "FullyConstrained", False)),
                    "geometry_count": len(obj.Geometry),
                    "constraints": [
                        {"index": index, "type": constraint.Type,
                         "name": getattr(constraint, "Name", ""),
                         "value": _json_value(getattr(constraint, "Value", None))}
                        for index, constraint in enumerate(obj.Constraints)
                    ],
                }

        if None in overall["min"]:
            overall = None
        return {
            "document": doc.Name, "label": doc.Label, "file": doc.FileName,
            "object_count": len(doc.Objects), "overall_bounding_box": overall,
            "objects": objects, "spreadsheets": spreadsheets, "sketches": sketches,
        }

    def _view_state(self, args):
        return _camera_snapshot(Gui.activeDocument().activeView())

    def _view_restore(self, args):
        _camera_apply(Gui.activeDocument().activeView(), args.get("state"))
        return True

    def _iter_shapes(self, doc, args, require_solid=False):
        """Yield (obj, shape) for the requested objects, or all visible shapes.

        Selection: args['objects'] (list of names/labels) or args['object'] (single),
        otherwise every visible object with a non-null shape.
        """
        names = args.get("objects")
        if args.get("object") and not names:
            names = [args["object"]]
        for obj in doc.Objects:
            if names is not None and obj.Name not in names and obj.Label not in names:
                continue
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull():
                continue
            if require_solid and not shape.Solids:
                continue
            if names is None and not getattr(obj, "Visibility", False):
                continue
            yield obj, shape

    def _mass_properties(self, args):
        """Volume, centre of mass and (optional) mass of solids.

        Pass args['density'] in kg/m^3 to get mass in grams. Without a density only
        volume + centre of mass are returned. Defaults to all visible solids.
        """
        doc = _document(args.get("document"))
        density = args.get("density")
        results, total_volume = [], 0.0
        for obj, shape in self._iter_shapes(doc, args, require_solid=True):
            vol = shape.Volume
            com = shape.CenterOfMass
            entry = {"name": obj.Name, "label": obj.Label,
                     "volume_mm3": vol,
                     "center_of_mass": [com.x, com.y, com.z]}
            if density is not None:
                entry["mass_g"] = float(density) * vol * 1e-9 * 1000.0
            total_volume += vol
            results.append(entry)
        out = {"objects": results, "total_volume_mm3": total_volume}
        if density is not None:
            out["density_kg_m3"] = float(density)
            out["total_mass_g"] = float(density) * total_volume * 1e-9 * 1000.0
        return out

    def _measure(self, args):
        """Minimum distance (and closest points) between two objects' shapes."""
        doc = _document(args.get("document"))
        a = _object(doc, args["object_a"])
        b = _object(doc, args["object_b"])
        info = a.Shape.distToShape(b.Shape)
        result = {"object_a": a.Name, "object_b": b.Name, "distance": info[0]}
        try:
            p1, p2 = info[1][0]
            result["point_a"] = [p1.x, p1.y, p1.z]
            result["point_b"] = [p2.x, p2.y, p2.z]
        except Exception:
            pass
        return result

    def _check_solid(self, args):
        """Report whether each object is a single, valid, watertight solid."""
        doc = _document(args.get("document"))
        results = []
        for obj, shape in self._iter_shapes(doc, args):
            solids = shape.Solids
            try:
                closed = bool(shape.isClosed())
            except Exception:
                closed = None
            valid = bool(shape.isValid())
            results.append({
                "name": obj.Name, "label": obj.Label,
                "shape_type": shape.ShapeType,
                "solid_count": len(solids),
                "is_valid": valid,
                "is_closed": closed,
                "single_watertight_solid": len(solids) == 1 and valid and closed is True,
                "volume_mm3": shape.Volume,
            })
        return {"objects": results,
                "all_single_watertight": bool(results) and all(r["single_watertight_solid"] for r in results)}

    def _interference_check(self, args):
        """Pairwise overlap (boolean common) volume between solids.

        Defaults to all visible solids; pass args['objects'] to limit. args['tolerance']
        (mm^3) sets the overlap volume above which a pair counts as interfering.
        """
        doc = _document(args.get("document"))
        tol = float(args.get("tolerance", 1e-6))
        solids = list(self._iter_shapes(doc, args, require_solid=True))
        pairs = []
        for i in range(len(solids)):
            for j in range(i + 1, len(solids)):
                oa, sa = solids[i]
                ob, sb = solids[j]
                try:
                    common = sa.common(sb)
                    vol = common.Volume if common is not None and not common.isNull() else 0.0
                    pairs.append({"a": oa.Name, "b": ob.Name,
                                  "overlap_volume_mm3": vol,
                                  "interferes": bool(vol > tol)})
                except Exception as exc:
                    pairs.append({"a": oa.Name, "b": ob.Name, "error": str(exc)})
        return {"pair_count": len(pairs),
                "any_interference": any(p.get("interferes") for p in pairs),
                "pairs": pairs}

    def _selection(self, args):
        return [_object_summary(obj) for obj in Gui.Selection.getSelection()]

    def _selection_set(self, args):
        doc = _document(args.get("document"))
        Gui.Selection.clearSelection()
        for name in args.get("objects", []):
            _object(doc, name)
            Gui.Selection.addSelection(doc.Name, name)
        return [obj.Name for obj in Gui.Selection.getSelection()]

    def _spreadsheet_set(self, args):
        doc = _document(args.get("document"))
        sheet = _object(doc, args["object"])
        sheet.set(args["cell"], str(args["value"]))
        if args.get("recompute", True):
            doc.recompute()
        return {"cell": args["cell"], "value": _json_value(sheet.get(args["cell"]))}

    def _spreadsheet_info(self, args):
        doc = _document(args.get("document"))
        sheet = _object(doc, args["object"])
        cells = list(sheet.getNonEmptyCells()) if hasattr(sheet, "getNonEmptyCells") else []
        return {"object": sheet.Name, "cells": {cell: _json_value(sheet.get(cell)) for cell in cells}}

    def _sketch_info(self, args):
        doc = _document(args.get("document"))
        sketch = _object(doc, args["object"])
        if not sketch.TypeId.startswith("Sketcher::"):
            raise TypeError("Object is not a Sketcher object: %s" % sketch.Name)
        return {
            "object": sketch.Name,
            "fully_constrained": bool(getattr(sketch, "FullyConstrained", False)),
            "solver_messages": str(getattr(sketch, "SolverMessages", "")),
            "geometry_count": len(sketch.Geometry),
            "constraint_count": len(sketch.Constraints),
            "constraints": [{"index": index, "type": constraint.Type, "value": _json_value(getattr(constraint, "Value", None))} for index, constraint in enumerate(sketch.Constraints)],
        }

    def _sketch_set_datum(self, args):
        doc = _document(args.get("document"))
        sketch = _object(doc, args["object"])
        quantity = App.Units.Quantity(str(args["value"]))
        sketch.setDatum(int(args["constraint"]), quantity)
        doc.recompute()
        return self._sketch_info(args)

    def _set_property(self, args):
        doc = _document(args.get("document"))
        obj = _object(doc, args["object"])
        setattr(obj, args["property"], args["value"])
        if args.get("recompute", True):
            doc.recompute()
        return _json_value(getattr(obj, args["property"]))

    def _set_expression(self, args):
        doc = _document(args.get("document"))
        obj = _object(doc, args["object"])
        obj.setExpression(args["property"], args.get("expression"))
        if args.get("recompute", True):
            doc.recompute()
        return {"property": args["property"], "expression": args.get("expression")}

    def _set_visibility(self, args):
        doc = _document(args.get("document"))
        obj = _object(doc, args["object"])
        obj.Visibility = bool(args["visible"])
        return {"object": obj.Name, "visible": obj.Visibility}

    def _create_object(self, args):
        doc = _document(args.get("document"))
        obj = doc.addObject(args["type_id"], args["name"])
        if args.get("label"):
            obj.Label = args["label"]
        for prop, value in args.get("properties", {}).items():
            setattr(obj, prop, value)
        doc.recompute()
        return _object_summary(obj, True)

    def _remove_object(self, args):
        doc = _document(args.get("document"))
        obj = _object(doc, args["object"])
        name = obj.Name
        doc.removeObject(name)
        doc.recompute()
        return {"removed": name}

    def _recompute(self, args):
        doc = _document(args.get("document"))
        changed = doc.recompute()
        return {"changed": changed, "modified": getattr(doc, "Modified", None)}

    def _save(self, args):
        doc = _document(args.get("document"))
        path = args.get("path")
        protected = self.protected_documents.get(doc.Name)
        target = os.path.abspath(path) if path else os.path.abspath(doc.FileName or "")
        if protected and target != protected:
            raise PermissionError("The supervised document may only be saved to its protected canonical path")
        if path:
            doc.saveAs(os.path.abspath(path))
        else:
            if not doc.FileName:
                raise ValueError("Document has no path; provide args.path")
            doc.save()
        return {"file_name": doc.FileName, "modified": getattr(doc, "Modified", None)}

    def _checkpoint_dir(self, doc):
        base = os.path.dirname(doc.FileName) if doc.FileName else os.getcwd()
        path = os.path.join(base, ".freecad-checkpoints", doc.Name)
        os.makedirs(path, exist_ok=True)
        return path

    def _checkpoint(self, args):
        doc = _document(args.get("document"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in args.get("label", "checkpoint"))
        path = os.path.join(self._checkpoint_dir(doc), "%s-%s.FCStd" % (stamp, label))
        doc.saveCopy(path)
        self._record_event("checkpoint", {"document": doc.Name, "path": path})
        return {"path": path}

    def _list_checkpoints(self, args):
        doc = _document(args.get("document"))
        directory = self._checkpoint_dir(doc)
        return [{"path": os.path.join(directory, name), "size": os.path.getsize(os.path.join(directory, name))} for name in sorted(os.listdir(directory), reverse=True) if name.endswith(".FCStd")]

    def _open_checkpoint(self, args):
        path = os.path.abspath(args["path"])
        if not os.path.isfile(path) or not path.endswith(".FCStd"):
            raise ValueError("Checkpoint does not exist or is not an FCStd file")
        doc = App.openDocument(path)
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        return {"document": doc.Name, "path": path}

    def _undo(self, args):
        doc = _document(args.get("document"))
        count = max(1, int(args.get("count", 1)))
        for _ in range(min(count, doc.UndoCount)):
            doc.undo()
        doc.recompute()
        return self._history(args)

    def _redo(self, args):
        doc = _document(args.get("document"))
        count = max(1, int(args.get("count", 1)))
        for _ in range(min(count, doc.RedoCount)):
            doc.redo()
        doc.recompute()
        return self._history(args)

    def _history(self, args):
        doc = _document(args.get("document"))
        return {"undo_count": doc.UndoCount, "undo_names": list(doc.UndoNames), "redo_count": doc.RedoCount, "redo_names": list(doc.RedoNames)}

    def _validate(self, args):
        return _validate_document(_document(args.get("document")))

    def _batch(self, args):
        doc = _document(args.get("document"))
        operations = args.get("operations", [])
        if not operations:
            return {"operations": [], "validation": _validate_document(doc)}
        checkpoint = self._checkpoint({"document": doc.Name, "label": args.get("label", "batch")}) if args.get("checkpoint", True) else None
        doc.openTransaction(args.get("label", "AgentSmith batch"))
        results = []
        try:
            for operation in operations:
                command = operation.get("command")
                if command in ("batch", "execute_python", "checkpoint", "undo", "redo"):
                    raise ValueError("Command not allowed inside batch: %s" % command)
                nested = {"token": self.token, "command": command, "args": operation.get("args", {})}
                results.append(self._dispatch(nested)["result"])
            validation = _validate_document(doc)
            if args.get("require_valid", True) and not validation["ok"]:
                raise RuntimeError("Batch validation failed: %s" % validation["errors"])
            doc.commitTransaction()
        except Exception:
            doc.abortTransaction()
            doc.recompute()
            raise
        return {"operations": results, "validation": validation, "checkpoint": checkpoint}

    def _events(self, args):
        since = int(args.get("since", 0))
        return {"latest": self.event_sequence, "events": [event for event in self.events if event["seq"] > since]}

    def _export(self, args):
        doc = _document(args.get("document"))
        objects = [_object(doc, name) for name in args["objects"]]
        path = os.path.abspath(args["path"])
        Part.export(objects, path)
        return {"path": path, "objects": [obj.Name for obj in objects]}

    def _fit_view(self, args):
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        return True

    def _set_view(self, args):
        view = Gui.activeDocument().activeView()
        orientation = str(args.get("orientation") or args.get("view") or "axonometric").lower()
        # Common synonyms agents actually use — be liberal in what we accept.
        orientation = {"isometric": "axonometric", "iso": "axonometric", "axo": "axonometric",
                       "back": "rear"}.get(orientation, orientation)
        methods = {"axonometric": view.viewAxonometric, "front": view.viewFront, "rear": view.viewRear, "top": view.viewTop, "bottom": view.viewBottom, "left": view.viewLeft, "right": view.viewRight}
        if orientation not in methods:
            raise ValueError("Unknown orientation: %s" % orientation)
        methods[orientation]()
        if args.get("fit", True):
            view.fitAll()
        return orientation

    def _screenshot(self, args):
        path = os.path.abspath(args["path"])
        width = int(args.get("width", 1200))
        height = int(args.get("height", 900))
        Gui.activeDocument().activeView().saveImage(path, width, height, "Current")
        return {"path": path, "width": width, "height": height}

    def _execute_python(self, args):
        if not self.allow_python:
            raise PermissionError("Python execution is disabled in the bridge panel")
        code = args.get("code", "")
        if self.protected_documents:
            forbidden = (
                "App.closeDocument", "FreeCAD.closeDocument", "App.openDocument",
                "FreeCAD.openDocument", ".saveAs(", "os.remove(", "os.unlink(",
                ".unlink(", "shutil.move(", "shutil.rmtree(", "os.rename(",
                "os.replace(",
            )
            match = next((item for item in forbidden if item in code), None)
            if match:
                raise PermissionError("Operation blocked by the supervised-file guard: %s" % match)
        scope = {"App": App, "Gui": Gui, "FreeCAD": App, "FreeCADGui": Gui}
        exec(compile(code, "<agentsmith-bridge>", "exec"), scope, scope)
        return _json_value(scope.get("result"))

    def _section(self, args):
        """Read-only cross-section evidence of internal geometry.

        Cuts each target solid with a plane at args['axis'] ('x'|'y'|'z', default 'z') and
        args['position'] (default = bounding-box midpoint of the target along that axis) and
        returns NUMERIC evidence of the section WITHOUT mutating the document: per object the
        closed-wire count, each wire's length + bounding box, and (best-effort) the filled
        section area. Nested wire bounding boxes let a task infer e.g. wall thickness.
        """
        import Part  # lazy: the module must import even when Part is unavailable
        doc = _document(args.get("document"))
        axis = str(args.get("axis", "z")).lower()
        axis_index = {"x": 0, "y": 1, "z": 2}.get(axis)
        if axis_index is None:
            raise ValueError("Unknown axis: %s (use 'x', 'y' or 'z')" % axis)
        normal = App.Vector(1.0 if axis == "x" else 0.0,
                            1.0 if axis == "y" else 0.0,
                            1.0 if axis == "z" else 0.0)
        position_arg = args.get("position")
        results = []
        for obj, shape in self._iter_shapes(doc, args, require_solid=False):
            try:
                box = shape.BoundBox
                bbox_min = [box.XMin, box.YMin, box.ZMin]
                bbox_max = [box.XMax, box.YMax, box.ZMax]
                if position_arg is None:
                    position = (bbox_min[axis_index] + bbox_max[axis_index]) / 2.0
                else:
                    position = float(position_arg)
                wires = []
                try:
                    wires = list(shape.slice(normal, position))
                except Exception:
                    wires = []
                wire_entries = []
                closed_wires = 0
                sec_min = [None, None, None]
                sec_max = [None, None, None]
                for wire in wires:
                    try:
                        is_closed = bool(wire.isClosed())
                    except Exception:
                        is_closed = False
                    if is_closed:
                        closed_wires += 1
                    try:
                        wbox = wire.BoundBox
                        wmin = [wbox.XMin, wbox.YMin, wbox.ZMin]
                        wmax = [wbox.XMax, wbox.YMax, wbox.ZMax]
                        for i in range(3):
                            sec_min[i] = wmin[i] if sec_min[i] is None else min(sec_min[i], wmin[i])
                            sec_max[i] = wmax[i] if sec_max[i] is None else max(sec_max[i], wmax[i])
                    except Exception:
                        wmin, wmax = None, None
                    wire_entries.append({
                        "length": getattr(wire, "Length", None),
                        "closed": is_closed,
                        "bbox": {"min": wmin, "max": wmax},
                    })
                # Best-effort filled section area. Nested wires (holes) make this fragile,
                # so any failure falls back to 0 and the wire evidence above is used instead.
                section_area = 0.0
                closed = [w for w in wires if getattr(w, "isClosed", lambda: False)()]
                if closed:
                    try:
                        section_area = Part.Face(closed).Area
                    except Exception:
                        try:
                            section_area = sum(Part.Face(w).Area for w in closed)
                        except Exception:
                            section_area = 0.0
                results.append({
                    "object": obj.Name,
                    "label": obj.Label,
                    "section_area": section_area,
                    "closed_wires": closed_wires,
                    "wire_count": len(wires),
                    "section_bbox": {"min": sec_min, "max": sec_max},
                    "wire_lengths": [entry["length"] for entry in wire_entries],
                    "wires": wire_entries,
                })
            except Exception as exc:
                results.append({"object": getattr(obj, "Name", "?"), "error": str(exc)})
        return {
            "document": doc.Name,
            "axis": axis,
            "position": None if position_arg is None else float(position_arg),
            "plane": {"normal": [normal.x, normal.y, normal.z], "axis": axis},
            "objects": results,
        }

    def _print_readiness(self, args):
        """Read-only numeric FDM-printability evidence for the current geometry.

        Reports, per target solid, how much surface faces downwards steeply enough to
        require support material for the given build direction. Nothing is mutated.

        ANGLE CONVENTION (read carefully before acting on the numbers):
          * up_axis (default 'z') is the BUILD DIRECTION: layers stack along +up, the
            print bed sits at the minimum up-coordinate of each shape.
          * For every face the outward normal n is taken at the face parameter-range
            midpoint and normalized; a 'Reversed' face orientation flips n.
          * A face is DOWN-FACING when dot(n, up) < 0 (its outward side looks toward the
            bed and would need support underneath it).
          * theta = degrees(acos(-dot(n_hat, up_hat))) is the overhang angle:
                theta == 0   -> face points straight down  (horizontal underside)
                theta == 90  -> vertical wall
            So LOWER theta is WORSE. This is the angle from the straight-down direction,
            NOT the classic "angle from the build plate".
          * overhang_deg (default 45) = maximum self-supporting angle measured FROM
            VERTICAL. A down-facing face therefore NEEDS SUPPORT when
                theta < (90 - overhang_deg)
            i.e. it is closer to a horizontal underside than the printable limit.
          * Exception: a face with theta ~= 0 (within 1e-3) that also sits at the shape's
            minimum up-coordinate is a flat BOTTOM resting on the bed. It is counted as
            bottom_area (bed contact, fine) and is NOT reported as an overhang. A flat
            underside that is NOT near the bed is a floating overhang and IS reported.

        Args: optional 'object'/'objects' (see _iter_shapes), 'up_axis' in
        {x,y,z,-x,-y,-z} default 'z', 'overhang_deg' default 45.0.
        """
        import math  # lazy: keep import cost off the module load path
        doc = _document(args.get("document"))
        up_axis = str(args.get("up_axis", "z")).lower()
        allowed_axes = {"x", "y", "z", "-x", "-y", "-z"}
        if up_axis not in allowed_axes:
            raise ValueError("Unknown up_axis: %s (use one of %s)" % (up_axis, sorted(allowed_axes)))
        try:
            overhang_deg = float(args.get("overhang_deg", 45.0))
        except (TypeError, ValueError):
            overhang_deg = 45.0
        support_threshold = 90.0 - overhang_deg  # theta below this NEEDS support

        axis_letter = up_axis.lstrip("+-")
        axis_index = {"x": 0, "y": 1, "z": 2}[axis_letter]
        axis_sign = -1.0 if up_axis.startswith("-") else 1.0
        components = [0.0, 0.0, 0.0]
        components[axis_index] = axis_sign
        up = App.Vector(*components)  # unit build-direction vector

        results = []
        for obj, shape in self._iter_shapes(doc, args, require_solid=False):
            try:
                box = shape.BoundBox
                bbox = {"min": [box.XMin, box.YMin, box.ZMin],
                        "max": [box.XMax, box.YMax, box.ZMax]}
                dims = [box.XLength, box.YLength, box.ZLength]
                min_bbox_dim = min(dims)
                # up-coordinate range of this shape (up is an axis-aligned unit vector)
                lo = bbox["min"][axis_index]
                hi = bbox["max"][axis_index]
                if axis_sign > 0:
                    up_min, up_max = lo, hi
                else:
                    up_min, up_max = -hi, -lo
                up_extent = up_max - up_min
                bed_tol = max(1e-3, 0.01 * up_extent)

                total_face_area = 0.0
                bottom_area = 0.0
                overhang_area = 0.0
                overhang_faces = 0
                offenders = []
                for face in shape.Faces:
                    try:
                        area = float(face.Area)
                    except Exception:
                        continue
                    total_face_area += area
                    try:
                        u0, u1, v0, v1 = face.ParameterRange
                        n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
                        length = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
                        if length <= 1e-12:
                            continue
                        n = App.Vector(n.x / length, n.y / length, n.z / length)
                        if str(getattr(face, "Orientation", "")) == "Reversed":
                            n = App.Vector(-n.x, -n.y, -n.z)
                        dot = n.x * up.x + n.y * up.y + n.z * up.z
                    except Exception:
                        continue
                    if dot >= 0.0:
                        continue  # up-facing or vertical-and-up: never needs support below it
                    # theta: 0 = straight-down underside, 90 = vertical wall
                    clamped = max(-1.0, min(1.0, -dot))
                    theta = math.degrees(math.acos(clamped))
                    try:
                        com = face.CenterOfMass
                        center = [com.x, com.y, com.z]
                    except Exception:
                        fb = face.BoundBox
                        center = [(fb.XMin + fb.XMax) / 2.0,
                                  (fb.YMin + fb.YMax) / 2.0,
                                  (fb.ZMin + fb.ZMax) / 2.0]
                    center_up = axis_sign * center[axis_index]
                    is_flat = theta < 1e-3
                    near_bed = (center_up - up_min) <= bed_tol
                    if is_flat and near_bed:
                        bottom_area += area
                        continue
                    if theta < support_threshold:
                        overhang_faces += 1
                        overhang_area += area
                        offenders.append({"area": area, "angle_deg": theta, "center": center})

                # worst first: most horizontal (lowest theta), ties broken by larger area
                offenders.sort(key=lambda e: (e["angle_deg"], -e["area"]))
                overhang_pct = (overhang_area / total_face_area * 100.0) if total_face_area > 0 else 0.0
                results.append({
                    "object": obj.Name,
                    "label": obj.Label,
                    "total_face_area": total_face_area,
                    "bottom_area": bottom_area,
                    "overhang_faces": overhang_faces,
                    "overhang_area": overhang_area,
                    "overhang_area_pct": overhang_pct,
                    "min_bbox_dim": min_bbox_dim,
                    "bbox": bbox,
                    "worst_overhangs": offenders[:20],
                })
            except Exception as exc:
                results.append({"object": getattr(obj, "Name", "?"), "error": str(exc)})
        return {
            "document": doc.Name,
            "up_axis": up_axis,
            "overhang_deg": overhang_deg,
            "convention": ("theta = degrees(acos(-dot(face_normal, up))); "
                           "0=straight-down underside (worst), 90=vertical wall. "
                           "Down-facing face (dot(n,up)<0) needs support when "
                           "theta < 90 - overhang_deg. A flat (theta~0) face at the "
                           "shape's minimum up-coordinate is bed contact, counted as "
                           "bottom_area, not an overhang."),
            "support_threshold_deg": support_threshold,
            "objects": results,
        }


class BridgePanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = BridgeServer(self)
        self.server.log_message.connect(self._log)
        self.server.state_changed.connect(self._state)
        self.server.safety_violation.connect(self._task_safety_violation)

        self.status = QtWidgets.QLabel("Stopped")
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(DEFAULT_PORT)
        self.toggle = QtWidgets.QPushButton("Start bridge")
        self.toggle.clicked.connect(self._toggle)
        self.python_box = QtWidgets.QCheckBox("Allow authenticated Python execution")
        self.python_box.setToolTip("Powerful mode. Enable only while actively collaborating; localhost and session token are still required.")
        self.python_box.toggled.connect(self._python_changed)
        self.access = QtWidgets.QComboBox()
        self.access.addItems(["Read only", "Edit", "Edit + Python"])
        self.access.setCurrentIndex(1)
        self.access.currentIndexChanged.connect(self._access_changed)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)

        self.port.setMaximumWidth(90)
        self.toggle.setMaximumWidth(110)
        bridge_group = QtWidgets.QGroupBox("FreeCAD bridge")
        bridge_layout = QtWidgets.QVBoxLayout(bridge_group)
        bridge_header = QtWidgets.QHBoxLayout()
        bridge_header.addWidget(self.status, 1)
        bridge_header.addWidget(QtWidgets.QLabel("Port"))
        bridge_header.addWidget(self.port)
        bridge_header.addWidget(self.access)
        bridge_header.addWidget(self.toggle)
        bridge_layout.addLayout(bridge_header)

        actions = QtWidgets.QHBoxLayout()
        checkpoint = QtWidgets.QPushButton("Checkpoint")
        checkpoint.clicked.connect(self._checkpoint)
        validate = QtWidgets.QPushButton("Validate")
        validate.clicked.connect(self._validate)
        undo = QtWidgets.QPushButton("Undo")
        undo.clicked.connect(self._undo)
        redo = QtWidgets.QPushButton("Redo")
        redo.clicked.connect(self._redo)
        for button in (checkpoint, validate, undo, redo):
            actions.addWidget(button)
        bridge_layout.addLayout(actions)
        reload_button = QtWidgets.QPushButton("Reload updated bridge")
        reload_button.clicked.connect(self._reload_bridge)
        actions.addWidget(reload_button)
        bridge_layout.addWidget(self.python_box)
        self.bridge_activity_label = QtWidgets.QLabel("Bridge activity")
        bridge_layout.addWidget(self.bridge_activity_label)
        self.log.setMaximumHeight(90)
        bridge_layout.addWidget(self.log)
        self.bridge_details = QtWidgets.QToolButton()
        self.bridge_details.setText("Zobrazit technické detaily")
        self.bridge_details.setCheckable(True)
        self.bridge_details.toggled.connect(self._toggle_bridge_details)
        bridge_layout.addWidget(self.bridge_details)
        self.python_box.hide()
        self.bridge_activity_label.hide()
        self.log.hide()

        self.codex_output = QtWidgets.QPlainTextEdit()
        self.codex_output.setReadOnly(True)
        self.codex_output.setMaximumBlockCount(1000)
        self.codex_prompt = QtWidgets.QPlainTextEdit()
        self.codex_prompt.setPlaceholderText("Napiš, co má asistent v otevřeném FreeCAD modelu udělat…")
        self.codex_prompt.setMaximumHeight(110)
        self.reference_input = QtWidgets.QPlainTextEdit()
        self.reference_input.setPlaceholderText("Reference (nepovinné): cesty k obrázkům nebo URL, každá na řádek…")
        self.reference_input.setMaximumHeight(54)
        self.backend = QtWidgets.QComboBox()
        self.model = QtWidgets.QComboBox()
        self.preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/AgentSmith")
        try:
            # One-time migration from the old "CodexBridge" preferences node.
            if not self.preferences.GetString("Backend", ""):
                legacy_prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CodexBridge")
                for legacy_key in ("Backend", "CaptureViews", "TaskBudgetSeconds", "ReviewerPass",
                                   "ReviewerAutofix", "Model_codex", "Model_claude", "Model_copilot"):
                    legacy_value = legacy_prefs.GetString(legacy_key, "")
                    if legacy_value:
                        self.preferences.SetString(legacy_key, legacy_value)
        except Exception:
            pass
        self.backends = self._detect_backends()
        for backend_id, backend in self.backends.items():
            label = "%s  ·  %s" % (backend["label"], backend["version"] or "nenalezeno")
            self.backend.addItem(label, backend_id)
            if not backend["available"]:
                self.backend.model().item(self.backend.count() - 1).setEnabled(False)
        saved_backend = self.preferences.GetString("Backend", "codex")
        saved_index = self.backend.findData(saved_backend)
        if saved_index >= 0 and self.backend.model().item(saved_index).isEnabled():
            self.backend.setCurrentIndex(saved_index)
        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.budget_choice = QtWidgets.QComboBox()
        self.budget_choice.setToolTip("Časový rozpočet úlohy. Po jeho vyčerpání watchdog backend ukončí a dokument vrátí zpět.")
        for budget_label, budget_seconds in (("Rychlá úprava (8 min)", 480), ("Nový díl (15 min)", 900), ("Sestava (25 min)", 1500)):
            self.budget_choice.addItem(budget_label, budget_seconds)
        saved_budget = self.preferences.GetString("TaskBudgetSeconds", str(LIVE_EDIT_BUDGET_SECONDS))
        budget_index = self.budget_choice.findData(int(saved_budget)) if str(saved_budget).isdigit() else -1
        if budget_index >= 0:
            self.budget_choice.setCurrentIndex(budget_index)
        self.budget_choice.currentIndexChanged.connect(self._persist_budget)
        self.reviewer_enabled = QtWidgets.QCheckBox("Reviewer")
        self.reviewer_enabled.setToolTip(
            "Po úspěšné úloze spustí nezávislý read-only ověřovací průchod: druhý agent porovná "
            "výsledek se zadáním a referencemi a vypíše verdikt (poradní, dokument nemění).")
        self.reviewer_enabled.setChecked(self.preferences.GetString("ReviewerPass", "0") == "1")
        self.reviewer_enabled.toggled.connect(self._persist_reviewer)
        self.autofix_enabled = QtWidgets.QCheckBox("Auto-oprava")
        self.autofix_enabled.setToolTip(
            "Když reviewer skončí verdiktem CONCERNS, spustí se automaticky JEDNO opravné kolo "
            "workera s nálezy reviewera v zadání. Opravné kolo už další auto-opravu nespouští.")
        self.autofix_enabled.setChecked(self.preferences.GetString("ReviewerAutofix", "0") == "1")
        self.autofix_enabled.toggled.connect(self._persist_autofix)
        self.view_current = QtWidgets.QCheckBox("Aktuální kamera")
        self.view_current.setToolTip("Pořídit referenční snímek z aktuální pozice kamery (výchozí).")
        self.view_orientation_boxes = {}
        for view_id in CAPTURE_ORIENTATIONS:
            box = QtWidgets.QCheckBox(view_id.capitalize())
            box.toggled.connect(self._persist_capture_views)
            self.view_orientation_boxes[view_id] = box
        self.view_current.toggled.connect(self._persist_capture_views)
        saved_views = set(filter(None, self.preferences.GetString("CaptureViews", "current").split(",")))
        self.view_current.setChecked("current" in saved_views or not saved_views)
        for view_id, box in self.view_orientation_boxes.items():
            box.setChecked(view_id in saved_views)
        self.codex_full_access = QtWidgets.QCheckBox("Povolit backendu plný lokální přístup pro práci s živým modelem")
        self.codex_full_access.setToolTip("Nutné pro přístup k localhost bridge. Zapínej pouze pro důvěryhodné modelovací úlohy.")
        self.codex_full_access.setChecked(True)
        self.codex_full_access.hide()
        self.codex_supervisor_status = QtWidgets.QLabel("Supervisor: idle")
        self.codex_supervisor_status.setWordWrap(True)
        self.task_status = QtWidgets.QLabel("Připraveno")
        self.task_status.setAlignment(QtCore.Qt.AlignCenter)
        self.task_elapsed = QtWidgets.QLabel("")
        self.task_progress = QtWidgets.QProgressBar()
        self.task_progress.setRange(0, 0)
        self.task_progress.setTextVisible(False)
        self.task_progress.setMaximumHeight(8)
        self.task_progress.hide()
        self.task_timer = QtCore.QTimer(self)
        self.task_timer.setInterval(1000)
        self.task_timer.timeout.connect(self._update_task_elapsed)
        self.history_timer = QtCore.QTimer(self)
        self.history_timer.setInterval(1000)
        self.history_timer.timeout.connect(self._refresh_document_history)
        self.file_guard_timer = QtCore.QTimer(self)
        self.file_guard_timer.setInterval(500)
        self.file_guard_timer.timeout.connect(self._check_task_guard)
        self.task_started_monotonic = None
        self.task_stop_requested = False
        self._set_task_state("idle", "Připraveno")
        self.codex_run = QtWidgets.QPushButton("Odeslat")
        self.codex_run.clicked.connect(self._run_codex)
        self.codex_stop = QtWidgets.QPushButton("Stop")
        self.codex_stop.setEnabled(False)
        self.codex_stop.clicked.connect(self._stop_codex)
        self.history_choice = QtWidgets.QComboBox()
        self.history_choice.setMinimumWidth(220)
        self.history_restore = QtWidgets.QPushButton("Vrátit před tento krok")
        self.history_restore.clicked.connect(self._restore_history_step)
        self.history_label = QtWidgets.QLabel("Historie: bez uložených kroků")
        history_row = QtWidgets.QHBoxLayout()
        history_row.addWidget(self.history_label)
        history_row.addWidget(self.history_choice, 1)
        history_row.addWidget(self.history_restore)
        codex_buttons = QtWidgets.QHBoxLayout()
        codex_buttons.addWidget(self.codex_run)
        codex_buttons.addWidget(self.codex_stop)
        chat_group = QtWidgets.QGroupBox("Chat")
        codex_layout = QtWidgets.QVBoxLayout(chat_group)
        backend_row = QtWidgets.QHBoxLayout()
        backend_row.addWidget(QtWidgets.QLabel("Backend"))
        backend_row.addWidget(self.backend, 1)
        backend_row.addWidget(QtWidgets.QLabel("Model"))
        backend_row.addWidget(self.model, 1)
        codex_layout.addLayout(backend_row)
        task_row = QtWidgets.QHBoxLayout()
        task_row.addWidget(self.task_status)
        task_row.addWidget(self.task_elapsed)
        task_row.addStretch(1)
        codex_layout.addLayout(task_row)
        codex_layout.addWidget(self.task_progress)
        codex_layout.addWidget(self.codex_output, 1)
        codex_layout.addWidget(self.codex_prompt)
        reference_row = QtWidgets.QHBoxLayout()
        reference_row.addWidget(QtWidgets.QLabel("Reference"))
        reference_row.addWidget(self.reference_input, 1)
        reference_row.addWidget(QtWidgets.QLabel("Rozpočet"))
        reference_row.addWidget(self.budget_choice)
        reference_row.addWidget(self.reviewer_enabled)
        reference_row.addWidget(self.autofix_enabled)
        codex_layout.addLayout(reference_row)
        views_row = QtWidgets.QHBoxLayout()
        views_row.addWidget(QtWidgets.QLabel("Snímky:"))
        views_row.addWidget(self.view_current)
        for view_id in CAPTURE_ORIENTATIONS:
            views_row.addWidget(self.view_orientation_boxes[view_id])
        views_row.addStretch(1)
        codex_layout.addLayout(views_row)
        codex_layout.addWidget(self.codex_supervisor_status)
        codex_layout.addLayout(history_row)
        codex_layout.addLayout(codex_buttons)

        self.codex_process = QtCore.QProcess(self)
        self.codex_process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.codex_process.readyReadStandardOutput.connect(self._codex_output_ready)
        self.codex_process.finished.connect(self._codex_finished)
        self.codex_process.errorOccurred.connect(self._codex_process_error)
        # Independent, advisory read-only "reviewer" pass (judge != defendant). It runs
        # AFTER a task succeeds, never touches the protected document (bridge forced to
        # read-only for its duration), and only appends a verdict to the chat/history.
        self.reviewer_process = QtCore.QProcess(self)
        self.reviewer_process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.reviewer_process.readyReadStandardOutput.connect(self._reviewer_output_ready)
        self.reviewer_process.finished.connect(self._reviewer_finished)
        self.reviewer_buffer = ""
        self.reviewer_state = None
        self.reviewer_run_id = 0
        self.codex_buffer = ""
        self.codex_task = None
        self.chat_seen = set()
        self.loaded_history_path = None
        self.active_document_path = "__unset__"

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(chat_group, 1)
        layout.addWidget(bridge_group, 0)
        self._backend_changed()
        self._refresh_document_history(True, True)
        self.history_timer.start()

    def _toggle(self):
        if self.server.server.isListening():
            self.server.stop()
        else:
            try:
                self.server.start(self.port.value())
            except Exception as exc:
                self._log("ERROR: %s" % exc)

    def _toggle_bridge_details(self, visible):
        self.python_box.setVisible(visible)
        self.bridge_activity_label.setVisible(visible)
        self.log.setVisible(visible)
        self.bridge_details.setText("Skrýt technické detaily" if visible else "Zobrazit technické detaily")

    def _python_changed(self, enabled):
        self.server.allow_python = enabled
        if enabled and self.access.currentIndex() < 2:
            self.access.setCurrentIndex(2)
        self._log("Python execution %s" % ("enabled" if enabled else "disabled"))

    def _access_changed(self, index):
        self.server.access_level = "read" if index == 0 else "edit"
        self.server.allow_python = index == 2
        self.python_box.blockSignals(True)
        self.python_box.setChecked(index == 2)
        self.python_box.blockSignals(False)
        self._log("Access level: %s" % self.access.currentText())

    def _state(self, running, port):
        self.status.setText("Running on 127.0.0.1:%d" % port if running else "Stopped")
        self.toggle.setText("Stop bridge" if running else "Start bridge")
        self.port.setEnabled(not running)

    def _log(self, message):
        self.log.appendPlainText(message)

    def _checkpoint(self):
        try:
            result = self.server._checkpoint({"label": "manual"})
            self._log("Checkpoint: %s" % result["path"])
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _validate(self):
        try:
            report = _validate_document(_document())
            self._log("Validation: %s (%d errors, %d warnings)" % ("OK" if report["ok"] else "FAILED", len(report["errors"]), len(report["warnings"])))
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _undo(self):
        try:
            _document().undo()
            _document().recompute()
            self._log("Undo")
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _redo(self):
        try:
            _document().redo()
            _document().recompute()
            self._log("Redo")
        except Exception as exc:
            self._log("ERROR: %s" % exc)

    def _reload_bridge(self):
        self._log("Reloading AgentSmith…")
        self._stop_codex()
        self.server.stop()
        dock = Gui.getMainWindow().findChild(QtWidgets.QDockWidget, "AgentSmithDock")
        if dock is not None:
            dock.hide()
            dock.setWidget(QtWidgets.QWidget())
            dock.deleteLater()
        module = __import__(__name__)
        import importlib
        importlib.reload(module)
        QtCore.QTimer.singleShot(100, module.show_panel)

    def _project_directory(self):
        doc = App.ActiveDocument
        return os.path.dirname(doc.FileName) if doc and doc.FileName else os.getcwd()

    def _harness_registry(self):
        """Load and cache the modeling-harness registry. Robust to a missing/broken file."""
        if getattr(self, "_harness_registry_cache", None) is not None:
            return self._harness_registry_cache
        registry = {"always_include": [], "playbooks": []}
        try:
            with open(os.path.join(HARNESS_DIR, "registry.json"), "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                registry["always_include"] = list(loaded.get("always_include", []))
                registry["playbooks"] = [p for p in loaded.get("playbooks", []) if p.get("id") and p.get("file")]
        except Exception as exc:
            self._log("Harness registry warning: %s" % exc)
        self._harness_registry_cache = registry
        return registry

    def _harness_text(self):
        """Assemble the full modeling harness: core methodology + the whole playbook
        library, framed so the backend applies each playbook conditionally.

        There is no exclusive mode: every playbook is always provided. A generated index
        lists each playbook with its trigger so the agent applies those whose trigger
        matches the part's character/use and ignores the rest.
        """
        registry = self._harness_registry()
        playbooks = registry.get("playbooks", [])

        def _read(name):
            try:
                with open(os.path.join(HARNESS_DIR, name), "r", encoding="utf-8") as handle:
                    return handle.read().strip()
            except Exception as exc:
                self._log("Harness file warning (%s): %s" % (name, exc))
                return ""

        sections = [_read(name) for name in registry.get("always_include", [])]
        if playbooks:
            index = [
                "## Harness library — apply what fits",
                "All playbooks below are ALWAYS provided; there is no single mode. Apply every "
                "playbook whose trigger matches the part's character or how it will be used, and "
                "ignore the ones that don't apply. Usually several apply at once (e.g. a printed "
                "load-bearing bracket → parametrics + strength + print3d + verify).",
            ]
            for playbook in playbooks:
                trigger = playbook.get("trigger")
                label = playbook.get("label", playbook["id"])
                index.append("- **%s** — apply when %s" % (label, trigger) if trigger else "- **%s**" % label)
            sections.append("\n".join(index))
            sections.extend(_read(playbook["file"]) for playbook in playbooks)

        label = "úplný harness (%d playbooků)" % len(playbooks)
        return label, "\n\n".join(section for section in sections if section)

    def _selected_capture_views(self):
        """Ordered list of reference views to capture, honoring the UI checkboxes."""
        views = []
        if self.view_current.isChecked():
            views.append("current")
        for view_id in CAPTURE_ORIENTATIONS:
            box = self.view_orientation_boxes.get(view_id)
            if box is not None and box.isChecked():
                views.append(view_id)
        if not views:
            views.append("current")
        return views

    def _persist_capture_views(self, *_):
        try:
            self.preferences.SetString("CaptureViews", ",".join(self._selected_capture_views()))
        except Exception:
            pass

    def _persist_reviewer(self, *_):
        try:
            self.preferences.SetString("ReviewerPass", "1" if self.reviewer_enabled.isChecked() else "0")
        except Exception:
            pass

    def _persist_autofix(self, *_):
        try:
            self.preferences.SetString("ReviewerAutofix", "1" if self.autofix_enabled.isChecked() else "0")
        except Exception:
            pass

    def _persist_budget(self, *_):
        try:
            self.preferences.SetString("TaskBudgetSeconds", str(int(self.budget_choice.currentData() or LIVE_EDIT_BUDGET_SECONDS)))
        except Exception:
            pass

    def _history_path(self, file_name=None):
        path = os.path.abspath(file_name or (App.ActiveDocument.FileName if App.ActiveDocument else ""))
        if not path:
            return None
        directory = os.path.join(os.path.dirname(path), ".freecad-history")
        return os.path.join(directory, os.path.basename(path) + ".jsonl")

    def _read_document_history(self, file_name=None):
        path = self._history_path(file_name)
        entries = []
        if path and not os.path.isfile(path):
            self._migrate_legacy_history(file_name, path)
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                        if isinstance(item, dict):
                            entries.append(item)
                    except Exception:
                        continue
        return entries

    def _migrate_legacy_history(self, file_name, destination):
        canonical = os.path.abspath(file_name or "")
        legacy = os.path.join(os.path.dirname(canonical), ".codex-freecad", "task-history.jsonl")
        if not canonical or not os.path.isfile(legacy):
            return
        migrated = []
        with open(legacy, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    old = json.loads(line)
                except Exception:
                    continue
                checkpoint = old.get("checkpoint", "")
                expected_dir = os.path.join(os.path.dirname(canonical), ".freecad-checkpoints", os.path.splitext(os.path.basename(canonical))[0])
                if checkpoint and os.path.commonpath([os.path.abspath(checkpoint), expected_dir]) == expected_dir:
                    old["checkpoint_before"] = old.get("checkpoint")
                    old["checkpoint_after"] = old.get("after_checkpoint")
                    old["canonical_path"] = canonical
                    old["migrated_from_legacy_audit"] = True
                    migrated.append(old)
        if migrated:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "w", encoding="utf-8") as handle:
                for item in migrated:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _append_document_history(self, file_name, entry):
        path = self._history_path(file_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with open(path, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self.loaded_history_path = None

    def _effective_history(self, entries):
        effective = []
        for item in entries:
            if item.get("status") == "success":
                effective.append(item)
            elif item.get("status") == "restored":
                target = item.get("target_task_id")
                index = next((i for i, previous in enumerate(effective) if previous.get("task_id") == target), len(effective))
                effective = effective[:index]
                effective.append(item)
        return effective

    def _refresh_document_history(self, force=False, show_chat=False):
        doc = App.ActiveDocument
        path = self._history_path(doc.FileName) if doc and doc.FileName else None
        switched = path != self.active_document_path
        if switched and not self.codex_task:
            # The active FreeCAD document changed: chat + history follow the file.
            self.active_document_path = path
            force = True
            show_chat = True
            self.codex_supervisor_status.setText("Supervisor: idle")
            self._set_task_state("idle", "Připraveno")
            self.task_elapsed.setText("")
        if not force and path == self.loaded_history_path:
            return
        self.loaded_history_path = path
        entries = self._read_document_history(doc.FileName) if doc and doc.FileName else []
        effective = self._effective_history(entries)
        successful = [item for item in effective if item.get("status") == "success" and item.get("checkpoint_before")]
        blocker = QtCore.QSignalBlocker(self.history_choice)
        self.history_choice.clear()
        for item in reversed(successful):
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.get("finished", 0)))
            label = "%s · %s" % (stamp, str(item.get("prompt", ""))[:80])
            self.history_choice.addItem(label, item)
        del blocker
        self.history_label.setText("Historie: %d kroků" % len(successful))
        self.history_choice.setEnabled(bool(successful) and not self.codex_task)
        self.history_restore.setEnabled(bool(successful) and not self.codex_task)
        if show_chat:
            self.codex_output.clear()
            if not doc:
                self.codex_output.appendPlainText("Žádný otevřený dokument. Otevři nebo vytvoř .FCStd model.")
            elif not (doc and doc.FileName):
                self.codex_output.appendPlainText("Nový neuložený dokument „%s“ — zatím bez historie. Ulož ho jako .FCStd a začni chatovat." % doc.Label)
            elif not entries:
                self.codex_output.appendPlainText("Dokument „%s“ — zatím bez historie. Napiš první úlohu." % doc.Label)
            else:
                self.codex_output.appendPlainText("Načten kontext dokumentu „%s“: %d záznamů, %d úspěšných změn.\n" % (doc.Label, len(entries), len(successful)))
                for item in entries[-8:]:
                    marker = "✓" if item.get("status") == "success" else "✗"
                    self.codex_output.appendPlainText("%s %s" % (marker, item.get("prompt", "bez popisu")))

    def _history_context(self, file_name):
        entries = self._read_document_history(file_name)
        relevant = self._effective_history(entries)[-12:]
        finished_ids = {item.get("task_id") for item in entries if item.get("status") != "pending"}
        interrupted = [item for item in entries if item.get("status") == "pending" and item.get("task_id") not in finished_ids]
        diagnostics = [item for item in entries if item.get("status") in ("failed", "interrupted")][-4:]
        relevant.extend(interrupted[-2:] + diagnostics)
        relevant = sorted({item.get("task_id", "row-%d" % index): item for index, item in enumerate(relevant)}.values(), key=lambda item: item.get("started", item.get("finished", 0)))[-16:]
        reviews = [item for item in entries if item.get("status") == "review" and item.get("verdict")][-2:]
        if not relevant and not reviews:
            return "No persisted change history exists for this document yet."
        compact = []
        for item in relevant:
            compact.append({
                "time": item.get("finished"), "status": item.get("status"),
                "request": item.get("prompt"), "result": item.get("response", "")[-1200:],
                "changed_objects": item.get("changed_objects", []),
                "checkpoint_after": item.get("checkpoint_after"),
            })
        # The independent reviewer's latest findings carry forward: the next task should
        # know what an audit of this document already flagged or confirmed.
        for item in reviews:
            compact.append({
                "time": item.get("finished"), "status": "review",
                "reviewer_verdict": item.get("verdict", "")[-1500:],
            })
        return json.dumps(compact, ensure_ascii=False, indent=2)

    def _restore_history_step(self):
        item = self.history_choice.currentData()
        doc = App.ActiveDocument
        if not item or not doc or self.codex_task:
            return
        checkpoint = item.get("checkpoint_before")
        if not _fcstd_archive_valid(checkpoint):
            QtWidgets.QMessageBox.critical(self, "Checkpoint není dostupný", "Uložený bod nelze otevřít nebo je poškozený.")
            return
        canonical = os.path.abspath(doc.FileName)
        with open(checkpoint, "rb") as handle:
            data = handle.read()
        App.closeDocument(doc.Name)
        _atomic_write_bytes(canonical, data)
        restored = App.openDocument(canonical)
        restored.recompute()
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        record = {"status": "restored", "finished": time.time(), "prompt": "Návrat před krok: " + item.get("prompt", ""),
                  "restored_from": checkpoint, "target_task_id": item.get("task_id")}
        self._append_document_history(canonical, record)
        self._refresh_document_history(True, True)

    def _detect_backends(self):
        definitions = [
            ("codex", "Codex", "codex"),
            ("claude", "Claude Code", "claude"),
            ("copilot", "GitHub Copilot", "copilot"),
        ]
        detected = {}
        for backend_id, label, command in definitions:
            path = shutil.which(command)
            version = ""
            if path:
                try:
                    completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3)
                    version = (completed.stdout or completed.stderr).strip().splitlines()[0]
                except Exception:
                    version = "verze neznámá"
            detected[backend_id] = {"label": label, "command": path, "version": version, "available": bool(path)}
        return detected

    def _backend_changed(self, index=None):
        backend_id = self.backend.currentData()
        backend = self.backends.get(backend_id, {})
        self.preferences.SetString("Backend", backend_id or "codex")
        blocker = QtCore.QSignalBlocker(self.model)
        self.model.clear()
        for model_id, label in self._models_for_backend(backend_id):
            self.model.addItem(label, model_id)
        saved_model = self.preferences.GetString("Model_" + str(backend_id), "")
        model_index = self.model.findData(saved_model)
        self.model.setCurrentIndex(model_index if model_index >= 0 else 0)
        del blocker
        self.codex_run.setText("Odeslat přes %s" % backend.get("label", "backend"))

    def _set_task_state(self, state, text):
        colors = {
            "idle": ("#5f6368", "#eef0f2"),
            "running": ("#0b57d0", "#dbe8ff"),
            "success": ("#137333", "#d9f2e3"),
            "failed": ("#b3261e", "#f9dedc"),
            "stopped": ("#8a4b00", "#ffead1"),
        }
        foreground, background = colors.get(state, colors["idle"])
        self.task_status.setText(text)
        self.task_status.setStyleSheet("QLabel { color: %s; background: %s; border-radius: 8px; padding: 3px 10px; font-weight: bold; }" % (foreground, background))
        running = state == "running"
        self.task_progress.setVisible(running)
        if not running:
            self.task_timer.stop()

    def _update_task_elapsed(self):
        if self.task_started_monotonic is None:
            self.task_elapsed.setText("")
            return
        elapsed = max(0, int(time.monotonic() - self.task_started_monotonic))
        self.task_elapsed.setText("%d:%02d" % divmod(elapsed, 60))

    def _check_task_guard(self):
        task = self.codex_task
        if not task:
            self.file_guard_timer.stop()
            return
        canonical = task.get("canonical_path")
        try:
            self.server.ensure_discovery()
        except Exception as exc:
            violation = "Bridge discovery could not be maintained: %s" % exc
        else:
            violation = None
        doc = _optional_document(task["document"])
        if doc is None:
            violation = "Backend closed the protected FreeCAD document"
        elif os.path.abspath(doc.FileName or "") != canonical:
            violation = "Backend changed the protected document path"
        if canonical and not os.path.isfile(canonical):
            task["missing_checks"] = task.get("missing_checks", 0) + 1
            if task["missing_checks"] >= 3:
                _atomic_write_bytes(canonical, task["file_backup_bytes"])
                violation = "Backend removed the protected FCStd file; in-memory backup was restored"
        else:
            task["missing_checks"] = 0
        elapsed = time.time() - task.get("started", time.time())
        budget_seconds = task.get("budget_seconds", LIVE_EDIT_BUDGET_SECONDS)
        rss_growth = max(0, _rss_bytes() - task.get("rss_before", 0))
        if elapsed > budget_seconds:
            violation = violation or "Task exceeded its %d minute live-editing budget" % max(1, budget_seconds // 60)
        elif rss_growth > 1024 * 1024 * 1024:
            violation = violation or "FreeCAD memory grew by more than 1 GiB during the task"
        if violation and not task.get("guard_violation"):
            task["guard_violation"] = violation
            self.codex_output.appendPlainText("\nFILE GUARD: " + violation)
            self._set_task_state("failed", "Ochrana souboru zasáhla")
            if self.codex_process.state() != QtCore.QProcess.NotRunning:
                self.codex_process.terminate()

    def _task_safety_violation(self, reason):
        task = self.codex_task
        if not task or task.get("guard_violation"):
            return
        task["guard_violation"] = reason
        self.codex_output.appendPlainText("\nSAFETY LIMIT: " + reason)
        self._set_task_state("failed", "Bezpečnostní limit zasáhl")
        if self.codex_process.state() != QtCore.QProcess.NotRunning:
            self.codex_process.terminate()

    def _restore_task_snapshot(self, task, reason):
        canonical = task["canonical_path"]
        current = _optional_document(task["document"])
        if current is not None:
            App.closeDocument(current.Name)
        _atomic_write_bytes(canonical, task["file_backup_bytes"])
        restored = App.openDocument(canonical)
        restored.recompute()
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        self.codex_output.appendPlainText("\nRESTORE: document restored from protected in-memory snapshot (%s)." % reason)
        return restored

    def _model_changed(self, index=None):
        backend_id = self.backend.currentData()
        if backend_id:
            self.preferences.SetString("Model_" + str(backend_id), str(self.model.currentData() or ""))

    def _models_for_backend(self, backend_id):
        models = [("", "Výchozí / Auto")]
        try:
            if backend_id == "codex":
                home = App.ConfigGet("UserHomePath")
                cache_path = os.path.join(home, ".codex", "models_cache.json")
                catalog = None
                if os.path.isfile(cache_path):
                    with open(cache_path, "r", encoding="utf-8") as handle:
                        catalog = json.load(handle)
                if not catalog:
                    completed = subprocess.run([self.backends[backend_id]["command"], "debug", "models"], capture_output=True, text=True, timeout=8)
                    catalog = json.loads(completed.stdout)
                for item in catalog.get("models", []):
                    if item.get("visibility", "list") == "list" and item.get("slug"):
                        models.append((item["slug"], "%s  ·  %s" % (item.get("display_name", item["slug"]), item["slug"])))
            elif backend_id == "claude":
                models.extend([
                    ("sonnet", "Sonnet · aktuální alias"),
                    ("opus", "Opus · aktuální alias"),
                    ("haiku", "Haiku · aktuální alias"),
                    ("fable", "Fable · aktuální alias"),
                ])
            elif backend_id == "copilot":
                completed = subprocess.run([self.backends[backend_id]["command"], "help", "config"], capture_output=True, text=True, timeout=8)
                in_models = False
                for line in completed.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("`model`:"):
                        in_models = True
                        continue
                    if in_models and stripped.startswith("`contextTier`:"):
                        break
                    if in_models and stripped.startswith('- "') and stripped.endswith('"'):
                        model_id = stripped[3:-1]
                        models.append((model_id, model_id))
        except Exception as exc:
            self._log("Model catalog warning (%s): %s" % (backend_id, exc))
        return models

    def _backend_launch(self, backend_id, model_id, project, context, visual_paths=None):
        backend = self.backends[backend_id]
        visual_paths = visual_paths or []
        if not backend["available"]:
            raise RuntimeError("Backend is not installed: %s" % backend["label"])
        attached_visuals = visual_paths[:5]
        if backend_id == "codex":
            args = ["exec", "--json", "--skip-git-repo-check", "-C", project, "-s", "danger-full-access", context]
            for path in attached_visuals:
                args.extend(["--image", path])
            if model_id:
                args[1:1] = ["-m", model_id]
        elif backend_id == "claude":
            # The claude CLI has no --image flag, but Claude Code can open image files
            # from disk with its Read tool. Surface the attached visuals as a header
            # block in the prompt so the model reads them before modeling.
            claude_context = context
            if attached_visuals:
                header = "ATTACHED IMAGES (you cannot see them inline — READ each file with your image-capable Read tool BEFORE modeling):\n"
                header += "".join("- %s\n" % path for path in attached_visuals)
                claude_context = header + "\n" + context
            args = ["-p", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions", "--no-session-persistence", claude_context]
            if model_id:
                args[1:1] = ["--model", model_id]
        elif backend_id == "copilot":
            args = ["-C", project, "-p", context, "--output-format", "json", "--allow-all", "--no-ask-user", "--no-color", "--no-auto-update"]
            for path in attached_visuals:
                args.extend(["--attachment", path])
            if model_id:
                args[2:2] = ["--model", model_id]
        else:
            raise ValueError("Unknown backend: %s" % backend_id)
        return backend["command"], args

    def _ensure_bridge_client(self, project):
        """Make sure the agent-facing tools exist in the task's working directory.

        The harness and prompts reference these by relative path; when a task runs
        from a project folder that never had them (discovered live: the reviewer burned
        budget hunting for a copy in a sibling directory), drop in the addon's copies.
        Ships: the bridge client, the slicer check tool and its config."""
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        # The .py tools are addon-owned: stale project copies are refreshed so bug
        # fixes propagate (copy2 preserves the source mtime, so an up-to-date copy is
        # never rewritten and a user-edited, newer file is left alone). The slicer
        # config is user-editable (printer hosts, custom profiles) — never overwrite.
        refreshable = ("freecad_bridge_client.py", "slice_check.py", "print_send.py")
        for name in refreshable + ("slicer-config.json",):
            try:
                target = os.path.join(project, name)
                source = os.path.join(addon_dir, name)
                if not os.path.isfile(source):
                    continue
                if os.path.isfile(target):
                    if name not in refreshable:
                        continue
                    if os.path.getmtime(target) >= os.path.getmtime(source):
                        continue
                shutil.copy2(source, target)
                self._log("Agent tool copied into project: %s" % target)
            except Exception as exc:
                self._log("Agent tool copy warning (%s): %s" % (name, exc))
        return os.path.join(project, "freecad_bridge_client.py")

    def _collect_references(self, doc, task_id):
        """Parse the reference input into local image paths + URLs and record a manifest.

        Returns a dict {dir, local_images, urls, manifest, skipped}. Any IO failure degrades
        to an empty reference set so a bad reference can NEVER abort the task.
        """
        empty = {"dir": "", "local_images": [], "urls": [], "manifest": "", "skipped": []}
        try:
            raw = self.reference_input.toPlainText()
        except Exception:
            return dict(empty)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return dict(empty)
        image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        source_images, urls, skipped = [], [], []
        for line in lines:
            lower = line.lower()
            if lower.startswith("http://") or lower.startswith("https://"):
                urls.append(line)
            elif os.path.isfile(line) and lower.endswith(image_exts):
                source_images.append(line)
            else:
                skipped.append(line)
        try:
            base = os.path.dirname(doc.FileName) if doc.FileName else self._project_directory()
            root = os.path.join(base, ".agentsmith", "reference", task_id)
            os.makedirs(root, exist_ok=True)
            copied = []
            used_names = set()
            for src in source_images:
                name = os.path.basename(src)
                target = os.path.join(root, name)
                if target in used_names or os.path.exists(target):
                    stem, ext = os.path.splitext(name)
                    index = 1
                    while True:
                        candidate = os.path.join(root, "%s-%d%s" % (stem, index, ext))
                        if candidate not in used_names and not os.path.exists(candidate):
                            target = candidate
                            break
                        index += 1
                shutil.copy2(src, target)
                used_names.add(target)
                copied.append(os.path.abspath(target))
            # Download URL references up front so they reach the backend as REAL vision
            # input at launch. A URL left as text is a reference in name only: codex-style
            # backends cannot add images mid-run, and (observed live) the worker simply
            # ignores "you may fetch" — so the panel fetches, and a failed download is
            # reported instead of silently dropped.
            downloaded_map = {}
            failed_urls = []
            for index, url in enumerate(urls):
                path = self._download_reference_image(url, root, index)
                if path:
                    copied.append(path)
                    downloaded_map[path] = url
                else:
                    failed_urls.append(url)
            manifest_path = os.path.join(root, "reference-manifest.json")
            manifest = {"task_id": task_id, "local_images": copied, "urls": urls,
                        "downloaded_from_urls": downloaded_map, "failed_urls": failed_urls,
                        "skipped": skipped, "note": "user-supplied references"}
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
            return {"dir": root, "local_images": copied, "urls": urls,
                    "failed_urls": failed_urls, "manifest": manifest_path, "skipped": skipped}
        except Exception as exc:
            self._log("Reference input warning: %s" % exc)
            return dict(empty)

    def _download_reference_image(self, url, root, index):
        """Fetch one reference URL into the task's reference dir. Returns the local
        path, or None when the URL is not a usable image (never raises)."""
        try:
            import urllib.request
            request = urllib.request.Request(url, headers={"User-Agent": "FreeCAD-AgentSmith/%s" % BRIDGE_VERSION})
            with urllib.request.urlopen(request, timeout=15) as response:
                content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
                data = response.read(15 * 1024 * 1024 + 1)
            if len(data) > 15 * 1024 * 1024:
                raise ValueError("image larger than 15 MiB")
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                         "image/bmp": ".bmp"}.get(content_type)
            if extension is None:
                url_ext = os.path.splitext(url.split("?", 1)[0])[1].lower()
                if url_ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                    extension = url_ext
                else:
                    raise ValueError("not an image (Content-Type %s)" % (content_type or "unknown"))
            target = os.path.join(root, "reference-url-%d%s" % (index + 1, extension))
            with open(target, "wb") as handle:
                handle.write(data)
            return os.path.abspath(target)
        except Exception as exc:
            self._log("Reference URL download failed (%s): %s" % (url, exc))
            return None

    def _create_visual_context(self, doc, task_id, phase, views=None):
        root = os.path.join(os.path.dirname(doc.FileName), ".agentsmith", "visual-context", task_id, phase)
        os.makedirs(root, exist_ok=True)
        views = views or ["current"]
        objects = []
        for obj in doc.Objects:
            item = {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId,
                    "visible": bool(getattr(obj, "Visibility", False))}
            shape = getattr(obj, "Shape", None)
            if shape is not None and not shape.isNull():
                box = shape.BoundBox
                item.update({"shape_type": shape.ShapeType, "solids": len(shape.Solids),
                             "volume": shape.Volume,
                             "bounding_box": {"min": [box.XMin, box.YMin, box.ZMin],
                                              "max": [box.XMax, box.YMax, box.ZMax]}})
            objects.append(item)
        try:
            digest = self.server._model_digest({"document": doc.Name})
        except Exception as exc:
            digest = {"error": str(exc)}
        manifest = {
            "document": doc.Name, "file": os.path.abspath(doc.FileName), "task_id": task_id,
            "phase": phase, "captured_views": views,
            "selection": [obj.Name for obj in Gui.Selection.getSelection()],
            "objects": objects, "model_digest": digest,
        }
        manifest_path = os.path.join(root, "scene-manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        view = Gui.activeDocument().activeView()
        camera_state = _camera_snapshot(view)
        orientation_methods = {
            "front": view.viewFront, "rear": view.viewRear, "top": view.viewTop,
            "bottom": view.viewBottom, "left": view.viewLeft, "right": view.viewRight,
        }
        images = []
        try:
            # Capture the current camera first, without disturbing it.
            if "current" in views:
                path = os.path.join(root, "current.png")
                view.saveImage(path, 1100, 850, "Current")
                images.append(path)
            for name in views:
                if name == "current":
                    continue
                orient = orientation_methods.get(name)
                if orient is None:
                    continue
                orient()
                view.fitAll()
                QtGui.QApplication.processEvents()
                path = os.path.join(root, name + ".png")
                view.saveImage(path, 1100, 850, "Current")
                images.append(path)
        finally:
            _camera_apply(view, camera_state)
            QtGui.QApplication.processEvents()
        return {"directory": root, "manifest": manifest_path, "images": images, "captured_views": views}

    def _run_codex(self):
        prompt = self.codex_prompt.toPlainText().strip()
        if not prompt or self.codex_process.state() != QtCore.QProcess.NotRunning:
            return
        if App.ActiveDocument is None:
            QtWidgets.QMessageBox.warning(self, "No document", "Open a FreeCAD document before starting an editing task.")
            return
        if not self.server.server.isListening():
            self.server.start(self.port.value())
        self.server.ensure_discovery()
        # A new edit task takes priority over any advisory reviewer still running —
        # stop it BEFORE switching the bridge back to edit mode so it can never mutate.
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self._stop_reviewer()
        self.access.setCurrentIndex(2)
        doc = App.ActiveDocument
        backend_id = self.backend.currentData()
        backend = self.backends[backend_id]
        model_id = str(self.model.currentData() or "")
        model_label = self.model.currentText()
        if not doc.FileName:
            QtWidgets.QMessageBox.warning(self, "Dokument není uložený", "Nejdřív dokument ulož jako .FCStd. Supervisor potřebuje pevnou kanonickou cestu pro ochranu a obnovu.")
            return
        doc.recompute()
        validation = _validate_document(doc)
        if not validation["ok"]:
            QtWidgets.QMessageBox.warning(self, "Dokument není platný", "Úlohu nelze bezpečně spustit, protože výchozí dokument neprošel validací.")
            return
        doc.save()
        canonical_path = os.path.abspath(doc.FileName)
        if not _fcstd_archive_valid(canonical_path):
            QtWidgets.QMessageBox.critical(self, "Neplatný FCStd", "Uložený dokument není platný FCStd archiv. Backend nebyl spuštěn.")
            return
        with open(canonical_path, "rb") as handle:
            protected_bytes = handle.read()
        task_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        checkpoint = self.server._checkpoint({"document": doc.Name, "label": "task-before-" + task_id})["path"]
        self.codex_task = {
            "id": task_id,
            "prompt": prompt,
            "document": doc.Name,
            "before_fingerprint": _document_fingerprint(doc),
            "before_undo_count": doc.UndoCount,
            "before_event_sequence": self.server.event_sequence,
            "checkpoint": checkpoint,
            "backend": backend_id,
            "model": model_id,
            "started": time.time(),
            "canonical_path": canonical_path,
            "file_backup_bytes": protected_bytes,
            "file_backup_sha256": hashlib.sha256(protected_bytes).hexdigest(),
            "missing_checks": 0,
            "assistant_messages": [],
            "raw_backend_output": [],
            "rss_before": _rss_bytes(),
            "budget_seconds": int(self.budget_choice.currentData() or LIVE_EDIT_BUDGET_SECONDS),
        }
        if getattr(self, "_autofix_pending", False):
            self._autofix_pending = False
            self.codex_task["autofix_round"] = True
        self.server.protected_documents[doc.Name] = canonical_path
        self.server.supervised_metrics = {"task_id": task_id, "mutation_events": 0, "object_events": {}}
        self._append_document_history(canonical_path, {
            "status": "pending", "task_id": task_id, "prompt": prompt,
            "backend": backend_id, "model": model_id, "started": self.codex_task["started"],
            "checkpoint_before": checkpoint, "canonical_path": canonical_path,
        })
        capture_views = self._selected_capture_views()
        self.codex_task["capture_views"] = capture_views
        self.codex_task["camera_before"] = _camera_snapshot(Gui.activeDocument().activeView())
        # Whether any visible solid existed before the task. If the task CREATES the
        # first geometry, restoring the old camera would show the user an empty view
        # (observed live) — in that case we frame the new model instead.
        had_visible_geometry = False
        try:
            for existing_obj in doc.Objects:
                existing_shape = getattr(existing_obj, "Shape", None)
                if (existing_shape is not None and not existing_shape.isNull()
                        and getattr(existing_shape, "Solids", None)
                        and getattr(existing_obj, "Visibility", False)):
                    had_visible_geometry = True
                    break
        except Exception:
            had_visible_geometry = True
        self.codex_task["had_visible_geometry_before"] = had_visible_geometry
        try:
            visual_context = self._create_visual_context(doc, task_id, "before", capture_views)
        except Exception as exc:
            visual_context = {"directory": "", "manifest": "", "images": []}
            self._log("Visual context warning: %s" % exc)
        self.codex_task["visual_before"] = visual_context
        try:
            reference = self._collect_references(doc, task_id)
        except Exception as exc:
            reference = {"dir": "", "local_images": [], "urls": [], "manifest": "", "skipped": []}
            self._log("Reference input warning: %s" % exc)
        self.codex_task["reference"] = reference
        reference_local_images = list(reference.get("local_images", []))
        reference_urls = list(reference.get("urls", []))
        doc.openTransaction("FreeCAD Chat task " + task_id)
        self.codex_task["transaction_open"] = True
        project = self._project_directory()
        self._ensure_bridge_client(project)
        document = doc.FileName
        kind_label, harness_text = self._harness_text()
        self.codex_task["task_kind"] = "auto"
        budget_minutes = max(1, self.codex_task["budget_seconds"] // 60)
        delivery_contract = (
            "TIME BUDGET & DELIVERY CONTRACT (read first):\n"
            "- You have a HARD wall-clock limit of about %d minutes from the moment this task started. "
            "When it is exceeded a watchdog terminates you (SIGTERM) and rolls the document back, so an "
            "unfinished investigation counts as a FAILED task with zero value.\n"
            "- Your job is to DELIVER a verified change, not to investigate exhaustively. Diagnose quickly, "
            "then act. The moment you have a plausible root cause, implement the fix through the bridge "
            "BEFORE doing any further analysis. Analysis that never produces a live mutation is a failure.\n"
            "- Budget your time: spend at most the first third on diagnosis, apply the fix in the middle third, "
            "and reserve the final third for validate/save/screenshot. If unsure how long you have been running, "
            "check the wall clock with `date` and compare against the start time.\n"
            "- Prefer a concrete, reversible parametric change early and iterate over it, rather than seeking "
            "perfect certainty first. Fix the general/driving cause (e.g. a wrong Spreadsheet parameter or "
            "sketch expression), not a hard-coded value for this one model.\n"
            "- If diagnosis shows the correct fix is to change a driving parameter, expression or constraint, "
            "MAKE that change through the bridge now; do not merely report what should be changed.\n"
            "- Only report FAILED without a mutation if the live document is genuinely unreachable or the request "
            "is impossible; in that case say so early instead of burning the whole budget.\n"
        ) % budget_minutes
        reference_section = ""
        if reference_local_images or reference_urls:
            reference_lines = ["USER-SUPPLIED REFERENCES (MANDATORY visual target):"]
            if reference_local_images:
                reference_lines.append(
                    "- %d reference image(s) are ATTACHED as vision input (URL references were already "
                    "downloaded for you). They are the PRIMARY statement of what the user wants the part "
                    "to LOOK like — matching them is part of the task, not an optional inspiration:\n"
                    "  1. STUDY them before planning: describe to yourself the topology, proportions and "
                    "functional features they show.\n"
                    "  2. Your feature plan MUST name which reference features you are reproducing "
                    "(counts, arrangement, curves, interfaces).\n"
                    "  3. Your final verification MUST put your axonometric render beside the references "
                    "and enumerate matches vs deviations. An unexplained divergence in topology or "
                    "proportion is a DEFECT to fix, not a footnote.\n"
                    "  They are NOT dimensional truth: derive every dimension/tolerance from FreeCAD "
                    "geometry and the Parameters spreadsheet, never by scaling pixels."
                    % len(reference_local_images)
                )
                for path in reference_local_images:
                    reference_lines.append("    · %s" % path)
            failed_urls = list(reference.get("failed_urls", []))
            if failed_urls:
                reference_lines.append(
                    "- These reference URL(s) could NOT be downloaded as images; fetch or research them "
                    "yourself and note in the report what you used:")
                for url in failed_urls:
                    reference_lines.append("    · %s" % url)
            elif reference_urls and not reference_local_images:
                reference_lines.append("- Reference URL(s):")
                for url in reference_urls:
                    reference_lines.append("    · %s" % url)
            reference_lines.append("- Reference manifest: %s" % (reference.get("manifest") or "(none)"))
            reference_lines.append("- Reference dir (the reference.md playbook may extend it): %s"
                                   % (reference.get("dir") or "(none)"))
            reference_section = "\n".join(reference_lines) + "\n\n"
        context = (
            "You are an editing worker (%s, selected model: %s) supervised by the FreeCAD Chat panel.\n"
            "Task id: %s\nProject: %s\nLive active document: %s\n"
            "%s\n"
            "MODELING HARNESS (%s) — always-on methodology; apply each playbook whose trigger matches the part:\n%s\n\n"
            "PERSISTED HISTORY FOR THIS DOCUMENT (oldest to newest):\n%s\n\n"
            "VISUAL/GEOMETRIC CONTEXT BEFORE EDIT:\nManifest: %s\nViews: %s\n"
            "The manifest embeds a `model_digest` (dimensional ground truth). Refresh it any time with "
            "`python3 freecad_bridge_client.py model_digest '{}'`.\n\n"
            "%s"
            "Bridge client: %s/freecad_bridge_client.py\nDiscovery: %s\nCheckpoint already created: %s\n\n"
            "MANDATORY WORKFLOW:\n"
            "1. Run `python3 freecad_bridge_client.py ping`, `capabilities`, `document_info`, and `model_digest` before acting.\n"
            "2. Diagnose only as much as needed to choose a fix, then modify the ALREADY OPEN document through "
            "freecad_bridge_client.py. Prefer `batch` for structured operations; use `execute_python` only when necessary. "
            "Do not merely edit generator scripts and do not open the FCStd in another FreeCAD process.\n"
            "3. Every actual model mutation must go through the bridge so it receives FreeCAD Undo transactions. Preserve native parametrism and follow AGENTS.md.\n"
            "   NEVER close or reopen the document, use saveAs, change its path, rename/move/delete the FCStd, or launch another FreeCAD process. The canonical file is protected by a live watchdog.\n"
            "4. Inspect the supplied images and scene manifest before choosing a method. Images are visual evidence, not dimensional proof. Derive task-specific measurements from FreeCAD geometry and numerically verify every requested dimension/tolerance.\n"
            "5. After editing, capture comparable views, inspect them for visible regressions, run bridge `validate`, then `save`, `fit_view`, and a final `screenshot`.\n"
            "6. Report exact changed FreeCAD objects, parameters, numerical checks, and visual comparison. If you cannot change or inspect the live document, report FAILED; never claim success based only on source-file edits.\n"
            "7. Manufacturing requests (slice / print / send-to-printer), alone or combined with modeling, are legitimate tasks — follow the 'Výroba' playbook and use slice_check.py / print_send.py from the project directory. If the task intentionally made NO model mutation (slice-only, print-only, status query), end your final message with the exact line 'ACTION-ONLY TASK COMPLETED: <summary>' so the supervisor accepts it; never use that line after a failure.\n\n"
            "USER REQUEST: %s"
            % (backend["label"], model_id or "backend default", task_id, project, document,
               delivery_contract,
               kind_label, harness_text or "(no harness playbook loaded)",
               self._history_context(document), visual_context["manifest"], json.dumps(visual_context["images"]),
               reference_section,
               project, DISCOVERY_FILE, checkpoint, prompt)
        )
        attached = list(visual_context["images"]) + reference_local_images
        program, arguments = self._backend_launch(backend_id, model_id, project, context, attached)
        self.codex_output.appendPlainText("\n› [%s / %s] %s\n" % (backend["label"], model_label, prompt))
        self.codex_output.appendPlainText("Supervisor checkpoint: " + checkpoint)
        self.codex_supervisor_status.setText("Supervisor: running task %s" % task_id)
        self.task_stop_requested = False
        self.task_started_monotonic = time.monotonic()
        self.task_elapsed.setText("0:00")
        self._set_task_state("running", "Pracuje · %s · %s" % (backend["label"], model_label))
        self.task_timer.start()
        self.file_guard_timer.start()
        self.codex_prompt.clear()
        self.codex_run.setEnabled(False)
        self.codex_stop.setEnabled(True)
        self.backend.setEnabled(False)
        self.model.setEnabled(False)
        self.codex_full_access.setEnabled(False)
        self.codex_buffer = ""
        self.chat_seen = set()
        self.codex_process.setWorkingDirectory(project)
        process_environment = QtCore.QProcessEnvironment.systemEnvironment()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
            process_environment.remove(variable)
        self.codex_process.setProcessEnvironment(process_environment)
        self.codex_process.start(program, arguments)
        self.codex_process.closeWriteChannel()

    def _stop_codex(self):
        if self.codex_process.state() != QtCore.QProcess.NotRunning:
            self.task_stop_requested = True
            self.task_status.setText("Zastavuji…")
            self.codex_process.terminate()
            QtCore.QTimer.singleShot(2500, self._kill_codex_if_running)
        elif self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.codex_output.appendPlainText("\nReviewer: zastavuji na žádost.")
            self._stop_reviewer()
            self.codex_stop.setEnabled(False)
            self.codex_supervisor_status.setText("Reviewer: zastaveno")

    def _kill_codex_if_running(self):
        if self.codex_process.state() != QtCore.QProcess.NotRunning:
            self.codex_process.kill()

    def _codex_output_ready(self):
        self.codex_buffer += bytes(self.codex_process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.codex_buffer.split("\n")
        self.codex_buffer = lines.pop()
        for line in lines:
            if self.codex_task and line.strip():
                self.codex_task["raw_backend_output"].append(line[-4000:])
                self.codex_task["raw_backend_output"] = self.codex_task["raw_backend_output"][-80:]
            try:
                event = json.loads(line)
                for text in self._backend_event_text(event):
                    key = (self.codex_task.get("backend") if self.codex_task else "", text)
                    if text and key not in self.chat_seen:
                        self.chat_seen.add(key)
                        self.codex_output.appendPlainText(text)
                        if self.codex_task:
                            self.codex_task["assistant_messages"].append(text)
            except Exception:
                self.codex_output.appendPlainText(line)

    def _backend_event_text(self, event, backend=None):
        if backend is None:
            backend = self.codex_task.get("backend") if self.codex_task else ""
        event_type = str(event.get("type", ""))
        texts = []
        if backend == "codex":
            if event_type == "item.completed":
                item = event.get("item", {})
                value = item.get("text") or item.get("content") or item.get("command")
                if value:
                    texts.append(str(value))
            elif event_type in ("turn.failed", "error"):
                texts.append("ERROR: " + str(event))
        elif backend == "claude":
            if event_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        texts.append(block["text"])
            elif event_type == "result" and event.get("is_error"):
                texts.append("ERROR: " + str(event.get("result") or event))
        elif backend == "copilot":
            if event_type == "assistant.message":
                candidate = event.get("data", {}).get("content") or event.get("message") or event.get("text") or event.get("content")
                if isinstance(candidate, str):
                    texts.append(candidate)
                elif isinstance(candidate, dict):
                    value = candidate.get("content") or candidate.get("text")
                    if isinstance(value, str):
                        texts.append(value)
            if "error" in event_type.lower():
                texts.append("ERROR: " + str(event))
        return texts

    def _codex_finished(self, exit_code, exit_status):
        self._codex_output_ready()
        camera_before = self.codex_task.get("camera_before") if self.codex_task else None
        had_geometry_before = self.codex_task.get("had_visible_geometry_before", True) if self.codex_task else True
        if self.codex_buffer.strip():
            trailing_output = self.codex_buffer.strip()
            self.codex_output.appendPlainText(trailing_output)
            if self.codex_task:
                self.codex_task["raw_backend_output"].append(trailing_output[-4000:])
        self.codex_buffer = ""
        backend_label = self.backends.get(self.codex_task.get("backend") if self.codex_task else "", {}).get("label", "Backend")
        self.codex_output.appendPlainText("\n%s finished (exit %d)." % (backend_label, exit_code))
        try:
            outcome = self._supervise_codex_result(exit_code)
        except Exception as exc:
            task = self.codex_task
            self.file_guard_timer.stop()
            if task:
                try:
                    self._restore_task_snapshot(task, "supervisor exception: %s" % exc)
                finally:
                    self.server.protected_documents.pop(task["document"], None)
                    self.server.supervised_metrics = None
                    self.codex_task = None
            outcome = {"status": "failed", "reason": "supervisor exception: %s" % exc, "restored": bool(task)}
            self.codex_output.appendPlainText("\nSUPERVISOR ERROR: " + str(exc))
        if self.task_stop_requested:
            self._set_task_state("stopped", "Zastaveno")
            self.codex_output.appendPlainText("\n■ ÚLOHA ZASTAVENA")
        elif outcome and outcome.get("status") == "success":
            self._set_task_state("success", "Hotovo · ověřeno")
            self.codex_output.appendPlainText("\n✓ ÚLOHA HOTOVA A OVĚŘENA")
        else:
            self._set_task_state("failed", "Selhalo · bez ověřené změny")
            self.codex_output.appendPlainText("\n✗ ÚLOHA SELHALA NEBO NEBYLA OVĚŘENA")
        self._update_task_elapsed()
        try:
            if Gui.activeDocument() is not None:
                view = Gui.activeDocument().activeView()
                task_succeeded = bool(outcome and outcome.get("status") == "success")
                if task_succeeded and not had_geometry_before:
                    # The task created the document's first geometry: make sure the new
                    # solids are visible and framed, instead of restoring a camera that
                    # was looking at an empty scene.
                    try:
                        active = App.ActiveDocument
                        for created_obj in (active.Objects if active else []):
                            created_shape = getattr(created_obj, "Shape", None)
                            if (created_shape is not None and not created_shape.isNull()
                                    and getattr(created_shape, "Solids", None)
                                    and not getattr(created_obj, "Visibility", False)
                                    # only top-level results: inputs consumed by a boolean/
                                    # feature (non-empty InList) stay hidden by design
                                    and not getattr(created_obj, "InList", None)):
                                created_obj.Visibility = True
                    except Exception as exc:
                        self._log("Visibility fixup warning: %s" % exc)
                    view.viewAxonometric()
                    view.fitAll()
                elif camera_before is not None:
                    _camera_apply(view, camera_before)
                QtGui.QApplication.processEvents()
        except Exception as exc:
            self._log("Camera restore warning: %s" % exc)
        self.codex_run.setEnabled(True)
        self.codex_stop.setEnabled(False)
        self.backend.setEnabled(True)
        self.model.setEnabled(True)
        self.codex_full_access.setEnabled(True)
        try:
            self._maybe_start_reviewer(outcome)
        except Exception as exc:
            self._log("Reviewer start warning: %s" % exc)

    def _maybe_start_reviewer(self, outcome):
        """After a VERIFIED-successful task, optionally run an independent read-only
        reviewer agent (judge != maker). It only inspects and appends an advisory
        verdict; it never mutates the document (the bridge is forced read-only)."""
        if self.task_stop_requested:
            return
        if not outcome or outcome.get("status") != "success":
            return
        if outcome.get("action_only"):
            return  # nothing was modeled; there is no geometry change to review
        if not self.reviewer_enabled.isChecked():
            return
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            return
        if self.codex_process.state() != QtCore.QProcess.NotRunning or self.codex_task:
            return
        if App.ActiveDocument is None:
            return
        backend_id = outcome.get("backend") or self.backend.currentData()
        backend = self.backends.get(backend_id)
        if not backend or not backend.get("available"):
            self.codex_output.appendPlainText("\nReviewer přeskočen: backend není dostupný.")
            return
        model_id = str(outcome.get("model") or "")
        project = self._project_directory()
        self._ensure_bridge_client(project)

        def _existing(paths, limit):
            found = []
            for candidate in (paths or []):
                try:
                    if candidate and os.path.isfile(candidate):
                        found.append(candidate)
                except Exception:
                    pass
                if len(found) >= limit:
                    break
            return found

        after_imgs = _existing((outcome.get("visual_after") or {}).get("images"), 6)
        before_imgs = _existing((outcome.get("visual_before") or {}).get("images"), 2)
        reference = outcome.get("reference") or {}
        ref_imgs = _existing(reference.get("local_images"), 4)
        images = after_imgs + ref_imgs + before_imgs

        ref_note = ""
        if ref_imgs or reference.get("urls"):
            ref_note = ("\nREFERENCE TARGET: %d reference image(s) attached and/or URLs %s. The user "
                        "supplied these as the intended look of the part. Compare the result renders "
                        "against them feature by feature (topology, proportions, functional elements). "
                        "If the model clearly diverges from the references and the worker's summary "
                        "gives no justification, that ALONE is VERDICT: CONCERNS — name the diverging "
                        "features concretely so the fix round can target them.\n"
                        % (len(ref_imgs), json.dumps(reference.get("urls") or [], ensure_ascii=False)))

        prompt = outcome.get("prompt", "")
        changed = ", ".join(outcome.get("changed_objects") or []) or "(none recorded)"
        worker_summary = (outcome.get("response") or "")[-1800:]

        review_context = (
            "You are an INDEPENDENT QA REVIEWER for a FreeCAD model. A worker agent just "
            "reported a task as done and the supervisor verified a valid live change. Judge "
            "whether that change actually satisfies the request and any reference — you are the "
            "judge, not the maker.\n\n"
            "HARD RULE: the bridge is in READ-ONLY mode for you. Do NOT modify, create, remove, "
            "recompute or save anything; mutating commands WILL be rejected. Only inspect.\n\n"
            "Bridge client: %s/freecad_bridge_client.py   Discovery: %s\n"
            "Use ONLY read commands: ping, capabilities, document_info, object_info, model_digest, "
            "measure, cross_section, check_solid, interference_check, mass_properties, validate, "
            "sketch_info, spreadsheet_info.\n\n"
            "ORIGINAL USER REQUEST:\n%s\n\n"
            "WHAT THE WORKER CHANGED: %s\n"
            "WORKER'S OWN SUMMARY (may be optimistic — verify it, do not trust it):\n%s\n"
            "%s\n"
            "ATTACHED IMAGES: result renders first, then any reference images, then a 'before' "
            "frame. Images are shape evidence only; confirm every number via model_digest / measure "
            "/ cross_section.\n\n"
            "Work quickly and decisively (a few minutes):\n"
            "1. `ping` + `model_digest` for the current geometry as ground truth.\n"
            "2. Verify the requested dimensions/features actually hold (measure; cross_section for "
            "internal walls/thickness).\n"
            "3. `validate` + `check_solid` for integrity; `mass_properties` for a physical sanity check.\n"
            "4. Compare the result render against the request and reference; list only REAL deviations.\n"
            "5. FUNCTIONAL SANITY (dimensions passing is NOT enough): mentally put the part into its "
            "real use. Identify the mounting/contact surface, the fastener axes, the load direction "
            "and the working feature (opening, hook, slot, cavity). Check they are mutually "
            "consistent — e.g. screw holes must be perpendicular to the mounting plane, a hook's "
            "opening must face away from the wall and oppose gravity, a lid must clear its box. A "
            "part whose features all measure correctly but which cannot physically do its job is a "
            "VERDICT: CONCERNS with a one-line explanation of why it fails in use.\n"
            "   PROJECTION CHECK: matching a reference in ONE view proves nothing. Compare the "
            "model's bounding box extents against the functional pose: the working feature must "
            "actually PROTRUDE along the away-from-mounting-surface axis (a wall part whose depth "
            "in that axis is only its material thickness is a flat lookalike — the profile was "
            "built in the wrong projection plane; classic failure, always check).\n\n"
            "OUTPUT (concise, no preamble): first line EXACTLY 'VERDICT: PASS' or 'VERDICT: CONCERNS', "
            "then up to ~6 bullets of concrete, checked findings (each with the measured number), then a "
            "'Suggested fixes:' line naming the specific Spreadsheet alias / sketch datum / expression to "
            "change and to what. If everything matches, say so briefly."
            % (project, DISCOVERY_FILE, prompt, changed, worker_summary, ref_note)
        )

        program, arguments = self._backend_launch(backend_id, model_id, project, review_context, images)

        self.reviewer_run_id += 1
        run_id = self.reviewer_run_id
        prev_access = self.access.currentIndex()
        self.access.setCurrentIndex(0)  # hard read-only guarantee for the reviewer
        self.reviewer_state = {"backend": backend_id, "model": model_id,
                               "prev_access_index": prev_access, "run_id": run_id,
                               "started": time.time(), "text": [],
                               "prompt": outcome.get("prompt", ""),
                               "autofix_round": bool(outcome.get("autofix_round"))}
        self.reviewer_buffer = ""
        self.codex_output.appendPlainText("\n── OVĚŘOVACÍ PRŮCHOD (reviewer) — nezávislý, read-only ──")
        self.codex_supervisor_status.setText("Reviewer: běží (read-only)")
        self.codex_stop.setEnabled(True)
        self.reviewer_process.setWorkingDirectory(project)
        process_environment = QtCore.QProcessEnvironment.systemEnvironment()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
            process_environment.remove(variable)
        self.reviewer_process.setProcessEnvironment(process_environment)
        self.reviewer_process.start(program, arguments)
        self.reviewer_process.closeWriteChannel()
        QtCore.QTimer.singleShot(240000, lambda rid=run_id: self._reviewer_timeout(rid))

    def _reviewer_output_ready(self):
        self.reviewer_buffer += bytes(self.reviewer_process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.reviewer_buffer.split("\n")
        self.reviewer_buffer = lines.pop()
        backend = (self.reviewer_state or {}).get("backend", "")
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                for text in self._backend_event_text(event, backend):
                    if text:
                        if self.reviewer_state is not None:
                            self.reviewer_state["text"].append(text)
                            self.reviewer_state["text"] = self.reviewer_state["text"][-60:]
                        self.codex_output.appendPlainText(text)
            except Exception:
                if self.reviewer_state is not None:
                    self.reviewer_state["text"].append(line)
                    self.reviewer_state["text"] = self.reviewer_state["text"][-60:]
                self.codex_output.appendPlainText(line)

    def _reviewer_finished(self, exit_code, exit_status):
        self._reviewer_output_ready()
        if self.reviewer_buffer.strip():
            trailing = self.reviewer_buffer.strip()
            self.codex_output.appendPlainText(trailing)
            if self.reviewer_state is not None:
                self.reviewer_state["text"].append(trailing)
        self.reviewer_buffer = ""
        state = self.reviewer_state
        self.reviewer_state = None
        try:
            self.access.setCurrentIndex(state.get("prev_access_index", 2) if state else 2)
        except Exception:
            pass
        self.codex_stop.setEnabled(False)
        self.codex_output.appendPlainText("\n── Konec ověřovacího průchodu ──")
        self.codex_supervisor_status.setText("Reviewer: hotovo")
        verdict_text = "\n".join((state or {}).get("text", []))[-6000:]
        try:
            doc = App.ActiveDocument
            if doc and doc.FileName and verdict_text.strip():
                self._append_document_history(os.path.abspath(doc.FileName), {
                    "status": "review", "finished": time.time(),
                    "backend": (state or {}).get("backend"), "model": (state or {}).get("model"),
                    "verdict": verdict_text, "exit_code": exit_code,
                })
                self._refresh_document_history(True)
        except Exception as exc:
            self._log("Reviewer history warning: %s" % exc)
        try:
            self._maybe_autofix(state, verdict_text)
        except Exception as exc:
            self._log("Autofix warning: %s" % exc)

    def _maybe_autofix(self, state, verdict_text):
        """One automatic corrective worker round when the reviewer found CONCERNS.

        Hard limits: only if the Auto-oprava checkbox is on, only after a task that was
        NOT itself a corrective round (never chains), and only when the panel is idle.
        The corrective run goes through the full normal task pipeline (checkpoint,
        watchdog, supervisor verification, and its own reviewer pass afterwards)."""
        if not state or state.get("autofix_round"):
            return
        if not self.autofix_enabled.isChecked():
            return
        if "VERDICT: CONCERNS" not in (verdict_text or ""):
            return
        if self.codex_task or self.codex_process.state() != QtCore.QProcess.NotRunning:
            return
        doc = App.ActiveDocument
        if doc is None or not doc.FileName:
            return
        original_prompt = state.get("prompt", "")
        findings = verdict_text.strip()[-2500:]
        corrective = (
            "CORRECTIVE ROUND (automatic, one-shot): an independent read-only reviewer "
            "checked the previous change and raised concerns. Fix ONLY the concrete "
            "deviations below — do not redesign the part, do not touch anything the "
            "reviewer did not flag, prefer the reviewer's 'Suggested fixes' parameters.\n\n"
            "ORIGINAL REQUEST (already largely implemented):\n%s\n\n"
            "REVIEWER FINDINGS TO RESOLVE:\n%s"
            % (original_prompt, findings)
        )
        self._autofix_pending = True
        self.codex_output.appendPlainText(
            "\nAUTO-OPRAVA: reviewer hlásí CONCERNS — spouštím jedno opravné kolo workera.")
        self.codex_prompt.setPlainText(corrective)
        try:
            self._run_codex()
        finally:
            # If _run_codex bailed before creating the task, the flag must not leak
            # into the next user-initiated run.
            self._autofix_pending = False

    def _reviewer_timeout(self, run_id):
        state = self.reviewer_state
        if state and state.get("run_id") == run_id and self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.codex_output.appendPlainText("\nReviewer: časový limit vypršel, ukončuji.")
            self.reviewer_process.terminate()
            QtCore.QTimer.singleShot(2000, self._kill_reviewer_if_running)

    def _kill_reviewer_if_running(self):
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.reviewer_process.kill()

    def _stop_reviewer(self):
        """Terminate a running reviewer without affecting the (already-finished) task."""
        self.reviewer_state = None
        self.reviewer_buffer = ""
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.reviewer_process.terminate()
            self.reviewer_process.waitForFinished(1500)
            if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
                self.reviewer_process.kill()

    def _codex_process_error(self, error):
        if self.codex_process.state() == QtCore.QProcess.NotRunning and self.codex_task:
            self._set_task_state("failed", "Backend se nepodařilo spustit")
            self.codex_output.appendPlainText("\nERROR: backend process could not start (%s)" % error)
            self.codex_run.setEnabled(True)
            self.codex_stop.setEnabled(False)
            self.backend.setEnabled(True)
            self.model.setEnabled(True)
            self.codex_full_access.setEnabled(True)
            self._supervise_codex_result(-1)

    def _supervise_codex_result(self, exit_code):
        task = self.codex_task
        self.file_guard_timer.stop()
        if not task:
            self.codex_supervisor_status.setText("Supervisor: no task metadata")
            return None
        canonical = task["canonical_path"]
        doc = _optional_document(task["document"])
        restore_reason = task.get("guard_violation")
        if doc is None:
            restore_reason = restore_reason or "active document was closed"
        elif os.path.abspath(doc.FileName or "") != canonical:
            restore_reason = restore_reason or "document path changed"
        elif not _fcstd_archive_valid(canonical):
            restore_reason = restore_reason or "canonical FCStd is missing or corrupt"

        if restore_reason:
            doc = self._restore_task_snapshot(task, restore_reason)
            outcome = {"status": "failed", "reason": restore_reason, "exit_code": exit_code,
                       "restored": True, "changed": False, "bridge_events": self.server.event_sequence - task["before_event_sequence"],
                       "validation_ok": _validate_document(doc)["ok"]}
        else:
            doc.recompute()
            validation = _validate_document(doc)
            after_fingerprint = _document_fingerprint(doc)
            changed = after_fingerprint != task["before_fingerprint"]
            bridge_events = self.server.event_sequence - task["before_event_sequence"]
            success = exit_code == 0 and changed and validation["ok"] and bridge_events > 0
            # Action-only contract: manufacturing/query tasks (slice, print, status)
            # legitimately change nothing. The worker signals intent with an exact
            # marker line. The fingerprint alone is NOT trusted here: Shape.hashCode()
            # can jitter on a mere recompute ("still-touched" features rebuild every
            # time — observed live: a successful slice-only task read as "changed").
            # The document observer's mutation counter is the real signal.
            metrics = self.server.supervised_metrics or {}
            observed_mutations = metrics.get("mutation_events", 0) if metrics.get("task_id") == task["id"] else None
            genuinely_unchanged = (not changed) or observed_mutations == 0
            action_only = (not success and exit_code == 0 and genuinely_unchanged
                           and validation["ok"]
                           and "ACTION-ONLY TASK COMPLETED" in "\n".join(task.get("assistant_messages", [])))
            if action_only:
                success = True
            outcome = {
                "status": "success" if success else "failed",
                "exit_code": exit_code,
                "changed": changed,
                "action_only": action_only,
                "bridge_events": bridge_events,
                "validation_ok": validation["ok"],
                "validation_errors": validation["errors"],
                "checkpoint": task["checkpoint"],
            }
            if action_only:
                # Nothing to commit or snapshot: just close the empty transaction.
                if doc.HasPendingTransaction:
                    doc.abortTransaction()
                    task["transaction_open"] = False
            elif success:
                if doc.HasPendingTransaction:
                    doc.commitTransaction()
                    task["transaction_open"] = False
                doc.save()
                if not _fcstd_archive_valid(canonical):
                    doc = self._restore_task_snapshot(task, "post-save FCStd integrity check failed")
                    outcome.update({"status": "failed", "reason": "post-save FCStd integrity check failed", "restored": True})
                else:
                    with open(canonical, "rb") as handle:
                        outcome["file_sha256"] = hashlib.sha256(handle.read()).hexdigest()
                    outcome["after_checkpoint"] = self.server._checkpoint({"document": doc.Name, "label": "task-after-" + task["id"]})["path"]
                    try:
                        outcome["visual_after"] = self._create_visual_context(doc, task["id"], "after", task.get("capture_views"))
                    except Exception as exc:
                        outcome["visual_after_error"] = str(exc)
            elif changed and (observed_mutations is None or observed_mutations > 0):
                doc = self._restore_task_snapshot(task, "backend result was not successfully verified")
                outcome["restored"] = True
            elif doc.HasPendingTransaction:
                doc.abortTransaction()
                doc.recompute()
                task["transaction_open"] = False
        outcome["guard_violation"] = task.get("guard_violation")
        outcome["canonical_path"] = canonical
        outcome["before_file_sha256"] = task["file_backup_sha256"]
        task_events = [event for event in self.server.events if event["seq"] > task["before_event_sequence"]]
        outcome["changed_objects"] = sorted(set(
            event.get("data", {}).get("object") for event in task_events
            if event.get("data", {}).get("object")
        ))
        outcome["response"] = "\n".join(task.get("assistant_messages", []))[-8000:]
        outcome["backend_output"] = "\n".join(task.get("raw_backend_output", []))[-12000:]
        outcome["checkpoint_before"] = task["checkpoint"]
        outcome["checkpoint_after"] = outcome.get("after_checkpoint")
        outcome["visual_before"] = task.get("visual_before")
        outcome["reference"] = task.get("reference")
        outcome["autofix_round"] = bool(task.get("autofix_round"))
        outcome.update({"task_id": task["id"], "backend": task.get("backend"), "model": task.get("model"), "prompt": task["prompt"], "started": task["started"], "finished": time.time()})
        project = self._project_directory()
        audit_dir = os.path.join(project, ".agentsmith")
        os.makedirs(audit_dir, exist_ok=True)
        with open(os.path.join(audit_dir, "task-history.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        self._append_document_history(canonical, outcome)
        if outcome["status"] == "success" and outcome.get("action_only"):
            message = "SUPERVISOR PASS: action-only task (slice/print/query) completed; document intentionally unchanged."
        elif outcome["status"] == "success":
            message = "SUPERVISOR PASS: live document changed, %d bridge events, geometry valid." % outcome["bridge_events"]
        else:
            message = "SUPERVISOR FAIL: no verified valid live-document change. " + json.dumps(outcome, ensure_ascii=False)
        self.codex_output.appendPlainText("\n" + message)
        self.codex_supervisor_status.setText(message)
        self.server.protected_documents.pop(task["document"], None)
        self.server.supervised_metrics = None
        self.codex_task = None
        self._refresh_document_history(True)
        return outcome

    def closeEvent(self, event):
        if self.codex_task:
            task = self.codex_task
            self.file_guard_timer.stop()
            if self.codex_process.state() != QtCore.QProcess.NotRunning:
                self.codex_process.kill()
                self.codex_process.waitForFinished(1500)
            self._restore_task_snapshot(task, "Chat panel closed during an active task")
            self.server.protected_documents.pop(task["document"], None)
            self.server.supervised_metrics = None
            self.codex_task = None
        if self.reviewer_process.state() != QtCore.QProcess.NotRunning:
            self.reviewer_state = None
            self.reviewer_process.kill()
            self.reviewer_process.waitForFinished(1000)
        self.server.stop()
        super().closeEvent(event)


class ShowPanelCommand:
    def GetResources(self):
        return {"MenuText": "Show AgentSmith panel", "ToolTip": "Show the AgentSmith control panel"}

    def Activated(self):
        show_panel()

    def IsActive(self):
        return True


def show_panel():
    global _panel
    main = Gui.getMainWindow()
    dock = main.findChild(QtWidgets.QDockWidget, "AgentSmithDock")
    if dock is None:
        dock = QtWidgets.QDockWidget("AgentSmith", main)
        dock.setObjectName("AgentSmithDock")
        _panel = BridgePanel(dock)
        dock.setWidget(_panel)
        main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    dock.show()
    dock.raise_()
    return dock


def register_commands():
    Gui.addCommand("AgentSmith_ShowPanel", ShowPanelCommand())
