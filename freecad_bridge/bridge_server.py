"""bridge_server.py -- the supervised socket bridge into the live FreeCAD document.

Everything in this module touches the live document: the JSON-per-line TCP server
the agent talks to, its ~45 commands, the document observer feeding the mutation
guard, and the geometry helpers those commands are built from.

Split out of BridgeGui.py, which had grown past four thousand lines holding two
unrelated things — this server and a Qt chat panel. They share almost nothing
beyond the document helpers at the top, which is why the seam is here.

The pure decision logic lives further out still, in the agentsmith_* modules that
import neither FreeCAD nor Qt and are covered by tests/. This file is deliberately
NOT that: it is the layer where the document actually gets changed.
"""

import hashlib
import json
import os
import secrets
import time
import traceback
import zipfile
from collections import deque

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtNetwork

import agentsmith_supervision

BRIDGE_VERSION = "0.18.1"
DEFAULT_PORT = 18421
DISCOVERY_FILE = "/tmp/freecad-agentsmith-bridge.json"
EVENT_FILE = "/tmp/freecad-agentsmith-events.jsonl"
# Camera fields captured/restored so a task returns the user's view exactly as it was.
_CAMERA_FIELDS = ("position", "orientation", "focalDistance", "height", "heightAngle")


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


# Object types that are construction scaffolding, never manufacturable geometry.
# A PartDesign Body ships with an Origin group holding three datum planes and three
# datum axes; FreeCAD reports each of them as Visibility=True with an INFINITE
# bounding box, even while the group that contains them is hidden. Counting them as
# geometry poisons every measurement built on "all visible shapes" — observed live:
# a correctly modelled hook reported overall_height = 2e+100, min_bbox_dim = 0, and
# failed watertight/single_solid because datum planes are not closed solids. Sketches
# are excluded for the same reason: they are inputs to geometry, not geometry.
_NON_GEOMETRY_TYPE_PREFIXES = (
    "App::Origin", "App::Line", "App::Plane", "App::Placement",
    "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point",
    "PartDesign::CoordinateSystem", "Sketcher::",
)


def _is_geometry_object(obj):
    """Is this object part of the model's manufacturable geometry?

    Excludes datums/origins/sketches (see above) and the intermediate features
    INSIDE a PartDesign Body: the Body already exposes the same final shape via
    its Tip, so counting both double-counts volume and mass and reports two solids
    where the user has one part.

    The Body-vs-feature choice is made per Body, on evidence rather than on type:
    only when the Body itself carries a usable shape are its features suppressed.
    A Body whose own shape is null (seen live — it happens when a feature is
    attached without a proper recompute) would otherwise make the entire part
    invisible to every measurement, which is far worse than double-counting; in
    that case the Body's Tip stands in for it.
    """
    type_id = str(getattr(obj, "TypeId", ""))
    if type_id.startswith(_NON_GEOMETRY_TYPE_PREFIXES):
        return False
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:
        parent = None
    if parent is None or not str(getattr(parent, "TypeId", "")).startswith("PartDesign::Body"):
        return True

    parent_shape = getattr(parent, "Shape", None)
    if parent_shape is not None and not parent_shape.isNull():
        return False  # the Body speaks for its features
    tip = getattr(parent, "Tip", None)
    return tip is not None and getattr(tip, "Name", None) == obj.Name


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
            violation = agentsmith_supervision.mutation_violation(
                metrics["mutation_events"], name, counts.get(name, 0), types.get(name, ""))
            if violation:
                self.safety_violation.emit(violation)
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
            "feature_probe": self._feature_probe,
        }
        if command not in handlers:
            raise ValueError("Unknown command: %s" % command)
        read_commands = {"ping", "documents", "document_info", "object_info", "model_digest", "mass_properties", "measure", "check_solid", "interference_check", "cross_section", "view_state", "selection", "history", "validate", "events", "capabilities", "list_checkpoints", "spreadsheet_info", "sketch_info", "print_readiness", "feature_probe"}
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
            "commands": ["ping", "documents", "document_info", "object_info", "model_digest", "mass_properties", "measure", "check_solid", "interference_check", "cross_section", "view_state", "view_restore", "selection", "selection_set", "spreadsheet_set", "spreadsheet_info", "sketch_info", "sketch_set_datum", "set_property", "set_expression", "set_visibility", "create_object", "remove_object", "recompute", "save", "checkpoint", "list_checkpoints", "open_checkpoint", "undo", "redo", "history", "validate", "batch", "events", "export", "fit_view", "set_view", "screenshot", "execute_python", "print_readiness", "feature_probe"],
            "features": {"transactions": True, "checkpoints": True, "event_journal": True, "chat_panel": True, "model_digest": True, "camera_restore": True, "measuring": True, "cross_section": True, "reference_inputs": True, "task_budget": True, "print_readiness": True, "feature_probe": True, "claude_vision_via_read": True},
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
                entry["is_geometry"] = _is_geometry_object(obj)
                # Datum planes have infinite bounding boxes; letting one into the
                # overall box makes every dimension read as 1e+100.
                if getattr(obj, "Visibility", False) and entry["is_geometry"]:
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
            if names is None:
                # Automatic selection: visible AND actually geometry. An explicit
                # name still wins, so a caller can always inspect a datum or a
                # single feature on purpose.
                if not getattr(obj, "Visibility", False) or not _is_geometry_object(obj):
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

    def _feature_probe(self, args):
        """Read-only inventory of the FUNCTIONAL features of the geometry.

        Reports the two things that decide whether a part can physically do its job,
        and that neither model_digest (bounding boxes) nor a render can answer:

          * `holes`   — coaxial cylindrical faces merged into one entry each: axis
            direction, a canonical point on the axis, diameter, axial extent, and
            whether the material lies outside the cylinder (a HOLE/bore) or inside it
            (a BOSS/pin). This is what makes "are the screw axes perpendicular to the
            mounting face?" a measurement instead of a guess.
          * `planes`  — planar faces at or above `min_plane_area_mm2`, with outward
            normal, area and centre of mass. The largest one is almost always the
            mounting/contact surface.

        Both were previously reconstructed ad hoc with `execute_python` loops by the
        worker, the reviewer and the grader — three implementations, three chances to
        be wrong. Lesson L1 (functional pose) and L2 (projection plane) are checkable
        from this output; see eval/run_eval.py for the checks built on it.

        Args: optional 'object'/'objects' (see _iter_shapes), 'min_plane_area_mm2'
        (default 1.0), 'tolerance' for coaxial grouping in mm (default 0.05).
        """
        import math  # lazy: keep import cost off the module load path
        doc = _document(args.get("document"))
        try:
            min_plane_area = float(args.get("min_plane_area_mm2", 1.0))
        except (TypeError, ValueError):
            min_plane_area = 1.0
        try:
            tolerance = max(1e-6, float(args.get("tolerance", 0.05)))
        except (TypeError, ValueError):
            tolerance = 0.05

        def _canonical_direction(vector):
            """Unit direction with a deterministic sign, so an axis and its reverse
            group together (a bore has no 'up' — only an orientation)."""
            length = vector.Length
            if length < 1e-9:
                return None
            direction = App.Vector(vector.x / length, vector.y / length, vector.z / length)
            for component in (direction.x, direction.y, direction.z):
                if abs(component) > 1e-9:
                    if component < 0:
                        direction = App.Vector(-direction.x, -direction.y, -direction.z)
                    break
            return direction

        results = []
        for obj, shape in self._iter_shapes(doc, args, require_solid=False):
            try:
                groups = {}
                planes = []
                for face in shape.Faces:
                    surface = getattr(face, "Surface", None)
                    type_id = type(surface).__name__ if surface is not None else ""

                    if type_id == "Cylinder":
                        direction = _canonical_direction(surface.Axis)
                        if direction is None:
                            continue
                        try:
                            u0, u1, v0, v1 = face.ParameterRange
                        except Exception:
                            continue  # no usable parametrisation: nothing to measure
                        radius = float(surface.Radius)
                        centre = surface.Center
                        # Closest point on the axis to the origin: independent of which
                        # point on the (infinite) axis the surface reports, and of the
                        # direction's sign — a stable grouping key.
                        offset = centre - direction * centre.dot(direction)
                        key = (round(direction.x / tolerance), round(direction.y / tolerance),
                               round(direction.z / tolerance), round(radius / tolerance),
                               round(offset.x / tolerance), round(offset.y / tolerance),
                               round(offset.z / tolerance))

                        # Material side: compare the face's outward normal with the
                        # radially-outward direction. Pointing inward (toward the axis)
                        # means the solid is outside the cylinder -> it is a hole.
                        internal = None
                        try:
                            # NOTE: face.normalAt() already returns the TRUE outward
                            # normal — verified live on a box (all six faces point away
                            # from the solid) and on a bore (Reversed faces point toward
                            # the axis, i.e. out of the material). Flipping on
                            # face.Orientation == "Reversed" double-negates it and
                            # reports every bore as a boss.
                            normal = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
                            point = face.valueAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
                            radial = point - (offset + direction * (point - offset).dot(direction))
                            if radial.Length > 1e-9 and normal.Length > 1e-9:
                                internal = normal.dot(radial) < 0
                        except Exception:
                            internal = None

                        # Axial extent from the face's own vertices: robust for a
                        # partial cylinder (counterbore step, chamfered mouth).
                        projections = [(vertex.Point - offset).dot(direction)
                                       for vertex in face.Vertexes]
                        span = (min(projections), max(projections)) if projections else (0.0, 0.0)

                        entry = groups.setdefault(key, {
                            "axis": [direction.x, direction.y, direction.z],
                            "axis_point": [offset.x, offset.y, offset.z],
                            "radius_mm": radius, "diameter_mm": 2.0 * radius,
                            "face_count": 0, "area_mm2": 0.0, "angle_deg": 0.0,
                            "_min": span[0], "_max": span[1], "_internal_votes": 0,
                            "_external_votes": 0,
                        })
                        entry["face_count"] += 1
                        entry["area_mm2"] += float(face.Area)
                        # Angular sweep, summed over the faces of the group. A drilled
                        # bore closes the full 360 deg (often as two half-cylinders); a
                        # curved lip, a rounded slot end or a filleted corner is a
                        # partial arc that is concave all the same. Without this, the
                        # J-curve of a wall hook reads as a 24 mm "hole" — seen live.
                        entry["angle_deg"] = min(
                            360.0, entry["angle_deg"] + math.degrees(abs(u1 - u0)))
                        entry["_min"] = min(entry["_min"], span[0])
                        entry["_max"] = max(entry["_max"], span[1])
                        if internal is True:
                            entry["_internal_votes"] += 1
                        elif internal is False:
                            entry["_external_votes"] += 1

                    elif type_id == "Plane":
                        area = float(face.Area)
                        if area < min_plane_area:
                            continue
                        normal = _canonical_direction(surface.Axis)
                        try:
                            u0, u1, v0, v1 = face.ParameterRange
                            outward = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
                            if outward.Length > 1e-9:
                                normal = App.Vector(outward.x / outward.Length,
                                                    outward.y / outward.Length,
                                                    outward.z / outward.Length)
                        except Exception:
                            pass
                        if normal is None:
                            continue
                        centre = face.CenterOfMass
                        planes.append({
                            "normal": [normal.x, normal.y, normal.z],
                            "area_mm2": area,
                            "center": [centre.x, centre.y, centre.z],
                        })

                holes = []
                for entry in groups.values():
                    length = entry.pop("_max") - entry.pop("_min")
                    internal_votes = entry.pop("_internal_votes")
                    external_votes = entry.pop("_external_votes")
                    entry["length_mm"] = length
                    # Ties and no-vote cases stay unknown rather than guessing: a caller
                    # filtering on kind should see None and skip, not be misled.
                    if internal_votes > external_votes:
                        entry["kind"] = "hole"
                    elif external_votes > internal_votes:
                        entry["kind"] = "boss"
                    else:
                        entry["kind"] = None
                    holes.append(entry)
                holes.sort(key=lambda item: (-item["diameter_mm"], -item["length_mm"]))
                planes.sort(key=lambda item: -item["area_mm2"])

                # How far the body stands off its largest flat face. That face is
                # almost always the mounting/contact surface, so this number is the
                # orientation-independent form of "does the working feature actually
                # protrude?" — a flat lookalike stands off by its own wall thickness
                # (lesson L2), a real hook by the depth of its arm. Reported here
                # rather than derived by callers because only the bridge has the
                # vertices; a bounding box cannot answer it for an arbitrary normal.
                if planes:
                    reference = planes[0]
                    normal = App.Vector(*reference["normal"])
                    origin = App.Vector(*reference["center"])
                    try:
                        distances = [abs((vertex.Point - origin).dot(normal))
                                     for vertex in shape.Vertexes]
                        reference["standoff_mm"] = max(distances) if distances else 0.0
                    except Exception:
                        reference["standoff_mm"] = None

                results.append({
                    "object": obj.Name, "label": obj.Label,
                    "holes": holes,
                    "planes": planes[:40],  # a fillet-heavy solid can have hundreds
                    "plane_count": len(planes),
                    "largest_plane": planes[0] if planes else None,
                })
            except Exception as exc:
                results.append({"object": obj.Name, "label": obj.Label, "error": str(exc)})
        return {"objects": results, "min_plane_area_mm2": min_plane_area}

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
                        # No Orientation flip: face.normalAt() already returns the true
                        # outward normal. Verified live on a box (all six faces point
                        # away from the solid centre) and on a bore (Reversed faces
                        # point toward the axis, i.e. out of the material). The flip
                        # that used to be here inverted overhang detection for exactly
                        # the Reversed faces that pocket floors and bore walls produce.
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
