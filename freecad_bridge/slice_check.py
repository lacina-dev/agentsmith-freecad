#!/usr/bin/env python3
"""slice_check.py -- real OrcaSlicer feedback for AgentSmith FreeCAD models.

Slices an STL/3MF (or the live FreeCAD model, via the bridge) for the user's
configured printer and reports the numbers a slicer actually computes: print
time, filament used, layer count, whether supports were generated, and any
slicer warnings. This is the strongest available evidence that a part is
printable -- it is the real slicer, not a geometric estimate.

Usage:
    python3 slice_check.py MODEL.stl [--json] [--keep]
    python3 slice_check.py MODEL.3mf
    python3 slice_check.py --from-bridge [--objects A,B] [--json]

Exit codes:
    0  slice succeeded (stats printed)
    1  slicer ran but failed (bad geometry, export error, ...)
    2  setup problem (orca missing, profiles not found, bridge unreachable)

Config (slicer-config.json, searched next to this script then in CWD) selects
the machine/process/filament profiles by name. See that file for details.

Stdlib only. OrcaSlicer 2.4.2, Prusa MK3.x family. Verified headless (no X
display needed).
"""

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APPIMAGE = "/home/robert/AppImages/OrcaSlicer_V2.4.2.AppImage"
CACHE_PROFILES = os.path.expanduser("~/.cache/agentsmith-orca/profiles")
DISCOVERY_FILE = "/tmp/freecad-agentsmith-bridge.json"
SLICE_TIMEOUT = 240
# Fields that identify a preset and must not be merged from parents / must be
# dropped when we flatten an inheritance chain into a self-contained preset.
_DROP_ON_FLATTEN = ("inherits", "instantiation")


class SetupError(Exception):
    """A precondition failed (exit 2): missing binary, profiles, bridge."""


class SliceError(Exception):
    """The slicer ran but did not produce a usable result (exit 1)."""


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config():
    for path in (os.path.join(HERE, "slicer-config.json"),
                 os.path.join(os.getcwd(), "slicer-config.json")):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                cfg = json.load(handle)
            cfg["_path"] = path
            return cfg
    raise SetupError(
        "slicer-config.json not found (looked next to slice_check.py and in the "
        "current directory). Create one naming the machine/process/filament "
        "profiles for your printer.")


def printer_table(cfg):
    """Normalized {key: printer-dict} — tolerates the legacy flat single-printer
    schema (a stale slicer-config.json copied into a project dir earlier)."""
    printers = cfg.get("printers")
    if isinstance(printers, dict) and printers:
        return printers
    if cfg.get("machine"):
        return {"default": {"label": cfg.get("machine"), "vendor": cfg.get("vendor", "Prusa"),
                            "machine": cfg["machine"], "process": cfg.get("process"),
                            "filament": cfg.get("filament")}}
    raise SetupError("slicer-config.json defines no printers.")


def select_printer(cfg, key):
    printers = printer_table(cfg)
    key = key or cfg.get("default_printer") or next(iter(printers))
    if key not in printers:
        raise SetupError(
            f"Printer {key!r} is not configured. Available: "
            + ", ".join(sorted(printers)) + ".")
    printer = dict(printers[key])
    if printer.get("unconfigured"):
        raise SetupError(
            f"Printer {key!r} ({printer.get('label', key)}) has no slicing profiles yet. "
            + (printer.get("note") or "Fill in machine/process/filament in slicer-config.json."))
    printer["_key"] = key
    return printer


def other_printers_line(cfg, current_key):
    """One line the agent can relay: what else the fleet can slice for."""
    entries = []
    for key, printer in sorted(printer_table(cfg).items()):
        if key == current_key:
            continue
        state = " — unconfigured (profiles pending)" if printer.get("unconfigured") else ""
        entries.append(f"{key} ({printer.get('label', key)}){state}")
    return "; ".join(entries)


# --------------------------------------------------------------------------- #
# Profile resolution: locate OrcaSlicer's system profiles, flatten inheritance
# --------------------------------------------------------------------------- #
def resolve_profiles_dir_for(cfg, printer):
    """Return a directory that contains <vendor>/{machine,process,filament}/*.json.

    Order: printer profiles_dir -> config profiles_dir -> env -> cache -> extract
    from the AppImage once. OrcaSlicer's presets live inside the AppImage; we
    extract only resources/profiles into ~/.cache and reuse it. A custom printer
    (e.g. user-supplied profiles) can point 'profiles_dir' at its own directory.
    """
    vendor = printer.get("vendor", "Prusa")
    candidates = []
    for source in (printer.get("profiles_dir"), cfg.get("profiles_dir"),
                   os.environ.get("AGENTSMITH_ORCA_PROFILES")):
        if source:
            candidates.append(os.path.expanduser(source))
    candidates.append(CACHE_PROFILES)
    for cand in candidates:
        if cand and os.path.isdir(os.path.join(cand, vendor)):
            return cand
    # Nothing cached -> extract from the AppImage.
    return extract_profiles(cfg)


def extract_profiles(cfg):
    appimage = cfg.get("orca_appimage") or DEFAULT_APPIMAGE
    if not os.path.isfile(appimage):
        raise SetupError(
            "OrcaSlicer profiles are not cached and the AppImage was not found "
            f"at {appimage!r}. Set 'orca_appimage' in slicer-config.json, or "
            f"point 'profiles_dir' / $AGENTSMITH_ORCA_PROFILES at an existing "
            "resources/profiles directory.")
    workdir = tempfile.mkdtemp(prefix="orca-extract-")
    try:
        # --appimage-extract PATTERN unpacks only the matching files.
        proc = subprocess.run(
            [appimage, "--appimage-extract", "resources/profiles/*"],
            cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=SLICE_TIMEOUT)
        src = os.path.join(workdir, "squashfs-root", "resources", "profiles")
        if proc.returncode != 0 or not os.path.isdir(src):
            raise SetupError(
                "Failed to extract OrcaSlicer profiles from the AppImage: "
                + proc.stderr.decode("utf-8", "replace")[-400:])
        os.makedirs(os.path.dirname(CACHE_PROFILES), exist_ok=True)
        if os.path.isdir(CACHE_PROFILES):
            shutil.rmtree(CACHE_PROFILES)
        shutil.move(src, CACHE_PROFILES)
        return CACHE_PROFILES
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _find_profile_file(profiles_dir, vendor, kind, name):
    path = os.path.join(profiles_dir, vendor, kind, name + ".json")
    if os.path.isfile(path):
        return path
    raise SetupError(
        f"{kind} profile {name!r} not found under {os.path.join(profiles_dir, vendor, kind)}. "
        f"Available: " + ", ".join(
            sorted(os.path.basename(p)[:-5]
                   for p in glob.glob(os.path.join(profiles_dir, vendor, kind, "*.json")))[:12])
        + " ...")


def flatten_profile(profiles_dir, vendor, kind, name):
    """Resolve an OrcaSlicer 'inherits' chain into one self-contained preset.

    OrcaSlicer's CLI does NOT resolve 'inherits' for presets passed by path --
    it silently falls back to hard defaults (e.g. filament_type PLA, no density
    -> 0 g). We therefore walk the chain ourselves (base files sit beside the
    leaf in the same kind/ directory), deep-overlay child over parent, and emit
    a self-contained preset. 'from' must stay 'system' or the CLI rejects it.
    """
    chain = []
    path = _find_profile_file(profiles_dir, vendor, kind, name)
    base_dir = os.path.dirname(path)
    seen = set()
    with open(path, "r", encoding="utf-8") as handle:
        node = json.load(handle)
    chain.append(node)
    while node.get("inherits"):
        parent = node["inherits"]
        if parent in seen:
            break
        seen.add(parent)
        ppath = os.path.join(base_dir, parent + ".json")
        if not os.path.isfile(ppath):
            raise SetupError(
                f"{kind} profile {name!r} inherits {parent!r}, which is missing "
                f"from {base_dir}. The cached profiles may be incomplete; delete "
                f"{CACHE_PROFILES} to force a fresh extraction.")
        with open(ppath, "r", encoding="utf-8") as handle:
            node = json.load(handle)
        chain.append(node)
    merged = {}
    for prof in reversed(chain):          # base first, leaf last (leaf wins)
        merged.update(prof)
    for key in _DROP_ON_FLATTEN:
        merged.pop(key, None)
    merged["from"] = "system"
    return merged


# --------------------------------------------------------------------------- #
# Bridge export (--from-bridge)
# --------------------------------------------------------------------------- #
def export_from_bridge(objects, dest_stl):
    """Export the live FreeCAD model to STL via the AgentSmith bridge client."""
    if not os.path.isfile(DISCOVERY_FILE):
        raise SetupError(
            f"FreeCAD bridge discovery file {DISCOVERY_FILE} not found. Start the "
            "AgentSmith bridge in FreeCAD (AgentSmith workbench -> Start bridge) "
            "before using --from-bridge.")
    call = _load_bridge_call()
    args = {"path": dest_stl}
    if objects:
        args["objects"] = objects
    try:
        response = call("export", args)
    except (OSError, ValueError) as exc:
        raise SetupError(f"FreeCAD bridge unreachable or rejected the request: {exc}")
    # The bridge's response shape is not contractually fixed here; treat an
    # explicit error field as fatal, otherwise trust the produced file.
    if isinstance(response, dict):
        err = response.get("error") or response.get("errors")
        ok = response.get("ok", response.get("success", True))
        if err or ok is False:
            raise SetupError(f"FreeCAD export failed: {err or response}")
    if not (os.path.isfile(dest_stl) and os.path.getsize(dest_stl) > 0):
        raise SetupError(
            "FreeCAD bridge reported no error but produced no STL "
            f"({dest_stl}). Check the object names passed with --objects.")
    return dest_stl


def _load_bridge_call():
    """Import call() from freecad_bridge_client.py; fall back to a tiny reimpl."""
    client = os.path.join(HERE, "..", "freecad_bridge_client.py")
    client = os.path.normpath(client)
    if os.path.isfile(client):
        spec = importlib.util.spec_from_file_location("freecad_bridge_client", client)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            if hasattr(module, "call"):
                return module.call
        except Exception:  # noqa: BLE001 - fall back to the inline protocol
            pass
    return _bridge_call_inline


def _bridge_call_inline(command, args=None):
    with open(DISCOVERY_FILE, "r", encoding="utf-8") as handle:
        discovery = json.load(handle)
    request = {"token": discovery["token"], "command": command, "args": args or {}}
    with socket.create_connection((discovery["host"], discovery["port"]), timeout=10) as conn:
        conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            block = conn.recv(65536)
            if not block:
                break
            buf += block
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


# --------------------------------------------------------------------------- #
# Slicing
# --------------------------------------------------------------------------- #
def run_slice(cfg, printer, model_path, workdir):
    profiles_dir = resolve_profiles_dir_for(cfg, printer)
    vendor = printer.get("vendor", "Prusa")
    flat_dir = os.path.join(workdir, "profiles")
    os.makedirs(flat_dir, exist_ok=True)
    flat = {}
    machine_name = None
    for kind, key in (("machine", "machine"), ("process", "process"),
                      ("filament", "filament")):
        name = printer.get(key)
        if not name:
            raise SetupError(
                f"Printer {printer.get('_key', '?')!r} is missing the {key!r} profile "
                "name in slicer-config.json.")
        merged = flatten_profile(profiles_dir, vendor, kind, name)
        if kind == "machine":
            machine_name = merged.get("name", name)
        else:
            # Same class of CLI quirk as unresolved 'inherits': by-path presets don't
            # get their 'compatible_printers_condition' expression evaluated (newer
            # vendor profiles like CORE One rely on it exclusively -> exit -17
            # "not compatible"). Replace the condition with an explicit name match,
            # which the CLI does honor.
            merged["compatible_printers"] = [machine_name]
            merged.pop("compatible_printers_condition", None)
        out = os.path.join(flat_dir, kind + ".json")
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(merged, handle)
        flat[kind] = out

    orca = cfg.get("orca_binary") or "orca-slicer"
    if not (os.path.isabs(orca) and os.path.isfile(orca)) and shutil.which(orca) is None:
        raise SetupError(
            f"OrcaSlicer binary {orca!r} not found on PATH. Set 'orca_binary' in "
            "slicer-config.json to the wrapper or AppImage path.")

    datadir = os.path.join(workdir, "datadir")
    outdir = os.path.join(workdir, "out")
    os.makedirs(datadir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)

    cmd = [
        orca,
        "--datadir", datadir,
        "--debug", "2",
        "--load-settings", flat["machine"] + ";" + flat["process"],
        "--load-filaments", flat["filament"],
    ]
    # Support policy: 'auto' lets the slicer add threshold-based support where the
    # geometry needs it -- so "supports generated" becomes a printability signal.
    support = str(cfg.get("support", "auto")).lower()
    if support in ("auto", "on", "true", "1"):
        cmd.append("--enable-support")
    cmd += ["--slice", "0", "--outputdir", outdir, model_path]

    try:
        # cwd=workdir: the Orca CLI drops 00000.log / result.json into its CWD --
        # keep those inside the temp dir instead of littering the project.
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=SLICE_TIMEOUT, cwd=workdir)
    except subprocess.TimeoutExpired:
        raise SliceError(f"OrcaSlicer timed out after {SLICE_TIMEOUT}s.")
    log = proc.stdout.decode("utf-8", "replace")

    gcodes = sorted(glob.glob(os.path.join(outdir, "*.gcode")))
    result_json = os.path.join(outdir, "result.json")
    result = {}
    if os.path.isfile(result_json):
        try:
            with open(result_json, "r", encoding="utf-8") as handle:
                result = json.load(handle)
        except (OSError, ValueError):
            result = {}

    if proc.returncode != 0 or not gcodes:
        detail = (result.get("error_string") or "").strip()
        tail = "\n".join(line for line in log.splitlines()[-15:] if line.strip())
        raise SliceError(
            f"OrcaSlicer failed (exit {proc.returncode})."
            + (f" {detail}" if detail and detail != "Success." else "")
            + (f"\n--- slicer log tail ---\n{tail}" if tail else ""))

    stats = parse_gcode(gcodes[0])
    stats.update(collect_warnings(result, log))
    stats["profiles"] = {
        "printer": printer.get("_key"),
        "printer_label": printer.get("label"),
        "machine": printer.get("machine"),
        "process": printer.get("process"),
        "filament": printer.get("filament"),
        "support_mode": support,
    }
    stats["gcode"] = gcodes[0]
    return stats


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _grab(pattern, text, cast=str, default=None):
    match = re.search(pattern, text)
    if not match:
        return default
    try:
        return cast(match.group(1).strip())
    except (ValueError, TypeError):
        return default


def parse_gcode(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    stats = {
        "print_time": _grab(r";\s*estimated printing time \(normal mode\)\s*=\s*(.+)", text),
        "first_layer_time": _grab(r";\s*estimated first layer printing time \(normal mode\)\s*=\s*(.+)", text),
        "filament_g": _grab(r";\s*total filament used \[g\]\s*=\s*([\d.]+)", text, float),
        "filament_mm": _grab(r";\s*filament used \[mm\]\s*=\s*([\d.]+)", text, float),
        "filament_cm3": _grab(r";\s*filament used \[cm3\]\s*=\s*([\d.]+)", text, float),
        "layers": _grab(r";\s*total layers count\s*=\s*(\d+)", text, int),
        "layer_height": _grab(r";\s*layer_height\s*=\s*([\d.]+)", text, float),
        "filament_type": _grab(r";\s*filament_type\s*=\s*([A-Za-z0-9]+)", text),
        "nozzle_temp": _grab(r";\s*nozzle_temperature\s*=\s*(\d+)", text, int),
    }
    # Real toolpath evidence (independent of the enable_support config flag):
    # OrcaSlicer tags extrusion moves with ;TYPE:<feature>.
    stats["support_lines"] = len(re.findall(r";TYPE:Support(?! interface)", text))
    stats["support_interface_lines"] = len(re.findall(r";TYPE:Support interface", text))
    stats["supports_generated"] = (stats["support_lines"] + stats["support_interface_lines"]) > 0
    stats["overhang_wall_regions"] = len(re.findall(r";TYPE:Overhang wall", text))
    stats["bridge_regions"] = len(re.findall(r";TYPE:(?:Bridge|Internal Bridge)", text))
    return stats


def collect_warnings(result, log):
    warnings = []
    for plate in result.get("sliced_plates", []) or []:
        msg = (plate.get("warning_message") or "").strip()
        if msg:
            warnings.append(f"plate {plate.get('id', '?')}: {msg}")
    err = (result.get("error_string") or "").strip()
    if err and err != "Success.":
        warnings.append(err)
    for line in log.splitlines():
        low = line.lower()
        if ("outside" in low and "bed" in low) or "empty layer" in low:
            warnings.append(line.strip())
    # De-duplicate, keep order.
    seen, unique = set(), []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return {"warnings": unique}


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def render_markdown(stats, model_name):
    p = stats["profiles"]
    printer_head = p.get("printer_label") or p.get("machine")
    lines = [f"# Slice check: {model_name}",
             f"Printer: **{printer_head}** (`--printer {p.get('printer') or 'default'}`)  |  "
             f"Process: **{p['process']}**  |  "
             f"Filament: **{p['filament']}** ({stats.get('filament_type') or '?'})",
             "",
             "Result: **SUCCESS**"]
    time = stats.get("print_time") or "?"
    lines.append(f"- Estimated print time: **{time}**"
                 + (f" (first layer {stats['first_layer_time']})" if stats.get("first_layer_time") else ""))
    g = stats.get("filament_g")
    mm = stats.get("filament_mm")
    cm3 = stats.get("filament_cm3")
    lines.append("- Filament: "
                 + (f"**{g:.2f} g**" if g is not None else "? g")
                 + (f"  |  {mm:.1f} mm" if mm is not None else "")
                 + (f"  |  {cm3:.2f} cm3" if cm3 is not None else ""))
    lh = stats.get("layer_height")
    lines.append(f"- Layers: **{stats.get('layers', '?')}**"
                 + (f" (layer height {lh} mm)" if lh else ""))
    if stats.get("supports_generated"):
        lines.append(f"- Supports generated: **YES** "
                     f"({stats['support_lines']} support + "
                     f"{stats['support_interface_lines']} interface regions) "
                     f"[support mode: {p['support_mode']}]")
    else:
        lines.append(f"- Supports generated: **no** [support mode: {p['support_mode']}]")
    if stats.get("overhang_wall_regions") or stats.get("bridge_regions"):
        lines.append(f"- Overhang-wall regions: {stats.get('overhang_wall_regions', 0)}  |  "
                     f"Bridge regions: {stats.get('bridge_regions', 0)}")
    warnings = stats.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("**Slicer warnings:**")
        lines.extend(f"- {w}" for w in warnings)
    else:
        lines.append("- Slicer warnings: none")
    if stats.get("gcode_saved"):
        lines.append(f"- G-code saved: `{stats['gcode_saved']}`")
    if stats.get("other_printers"):
        lines.append(f"- Other configured printers: {stats['other_printers']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Real OrcaSlicer feedback for a model (or the live FreeCAD model).")
    parser.add_argument("model", nargs="?", help="Path to an .stl or .3mf model.")
    parser.add_argument("--from-bridge", action="store_true",
                        help="Export the current FreeCAD model via the AgentSmith bridge, then slice.")
    parser.add_argument("--objects", default="",
                        help="Comma-separated object names to export (with --from-bridge).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--keep", action="store_true", help="Keep the temp work directory.")
    parser.add_argument("--printer", default="",
                        help="Printer key from slicer-config.json (default: config default_printer).")
    parser.add_argument("--list-printers", action="store_true",
                        help="List configured printers and exit.")
    parser.add_argument("--outputdir", default="",
                        help="Persist the produced G-code into this directory (created if missing).")
    opts = parser.parse_args(argv)

    if opts.list_printers:
        try:
            cfg = load_config()
        except SetupError as exc:
            _emit_error(opts.json, 2, str(exc))
            return 2
        default_key = cfg.get("default_printer") or ""
        rows = []
        for key, printer in sorted(printer_table(cfg).items()):
            state = "unconfigured (profiles pending)" if printer.get("unconfigured") else "ready"
            mark = " [default]" if key == default_key else ""
            rows.append({"key": key, "label": printer.get("label", key), "state": state,
                         "default": key == default_key})
            if not opts.json:
                print(f"{key}{mark}: {printer.get('label', key)} — {state}")
        if opts.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not opts.from_bridge and not opts.model:
        parser.error("give a model file, or --from-bridge.")
    if opts.from_bridge and opts.model:
        parser.error("use either a model file or --from-bridge, not both.")

    workdir = tempfile.mkdtemp(prefix="slice-check-")
    try:
        cfg = load_config()
        printer = select_printer(cfg, opts.printer.strip() or None)

        if opts.from_bridge:
            model_name = "FreeCAD model (bridge export)"
            model_path = os.path.join(workdir, "bridge_model.stl")
            objs = [o.strip() for o in opts.objects.split(",") if o.strip()]
            export_from_bridge(objs, model_path)
        else:
            model_path = os.path.abspath(opts.model)
            model_name = os.path.basename(model_path)
            if not os.path.isfile(model_path):
                raise SetupError(f"Model file not found: {opts.model}")
            if os.path.splitext(model_path)[1].lower() not in (".stl", ".3mf", ".obj", ".step", ".stp"):
                raise SetupError(
                    f"Unsupported model type {os.path.splitext(model_path)[1]!r}; "
                    "give an .stl or .3mf.")

        stats = run_slice(cfg, printer, model_path, workdir)
        stats["other_printers"] = other_printers_line(cfg, printer["_key"])

        if opts.outputdir:
            try:
                outdir = os.path.abspath(os.path.expanduser(opts.outputdir))
                os.makedirs(outdir, exist_ok=True)
                stem = os.path.splitext(os.path.basename(model_path))[0]
                saved = os.path.join(outdir, "%s.%s.gcode" % (stem, printer["_key"]))
                shutil.copy2(stats["gcode"], saved)
                stats["gcode_saved"] = saved
            except OSError as exc:
                stats.setdefault("warnings", []).append(
                    f"could not persist G-code to {opts.outputdir!r}: {exc}")

        if opts.json:
            print(json.dumps(stats, indent=2))
        else:
            print(render_markdown(stats, model_name))
        return 0

    except SetupError as exc:
        _emit_error(opts.json, 2, str(exc))
        return 2
    except SliceError as exc:
        _emit_error(opts.json, 1, str(exc))
        return 1
    finally:
        if opts.keep:
            sys.stderr.write(f"[kept work dir: {workdir}]\n")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _emit_error(as_json, code, message):
    if as_json:
        print(json.dumps({"ok": False, "exit": code, "error": message}, indent=2))
    else:
        sys.stderr.write(f"slice_check: {message}\n")


if __name__ == "__main__":
    sys.exit(main())
