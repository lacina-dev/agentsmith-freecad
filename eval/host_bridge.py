#!/usr/bin/env python3
"""Start (or reuse) a FreeCAD instance hosting the AgentSmith bridge for eval runs.

The one rule this tool exists to enforce: NEVER launch a second FreeCAD when a
live bridge already answers. The ad-hoc predecessor of this script launched an
AppImage, did not wait for the (slow, ~30-60 s cold) boot, concluded failure and
launched again — leaving two GUI instances fighting over one discovery file.

Usage:
    python3 eval/host_bridge.py            # reuse a live bridge, else launch one
    python3 eval/host_bridge.py --status   # report; exit 0 live / 3 not live
    python3 eval/host_bridge.py --stop     # stop the instance this tool launched
    python3 eval/host_bridge.py --replace  # --stop, then launch fresh

Dual mode: with FreeCAD importable (i.e. run BY FreeCAD as a startup script) it
boots the bridge panel instead — Edit + Python access, because that is what
run_e2e.py requires. Do not point it at a document you care about; eval runs
create and mutate their own sandbox documents.

Exit codes: 0 bridge live (reused or launched) · 2 cannot launch · 3 not live
(--status) · 4 another launcher holds the lock.
"""

import json
import os
import socket
import subprocess
import sys
import time

DISCOVERY_FILE = "/tmp/freecad-agentsmith-bridge.json"
STATE_FILE = "/tmp/freecad-agentsmith-host.json"
LOCK_FILE = "/tmp/freecad-agentsmith-host.lock"
LOG_FILE = "/tmp/freecad-agentsmith-host.log"
APPIMAGE_ENV = "AGENTSMITH_FREECAD"
APPIMAGE_SEARCH_DIRS = ("~/AppImages", "~/Applications", "~/.local/bin", "~/Downloads")
READY_TIMEOUT = 180  # cold AppImage start: mount + Qt + addon imports takes tens of seconds


# --------------------------------------------------------------------------- probing

def read_discovery(path=DISCOVERY_FILE):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def ping(discovery, timeout=5):
    """True when the bridge described by the discovery record answers 'ping'."""
    if not discovery:
        return False
    request = {"token": discovery.get("token"), "command": "ping", "args": {}}
    try:
        with socket.create_connection((discovery.get("host", "127.0.0.1"),
                                       discovery["port"]), timeout=timeout) as conn:
            conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = b""
            while b"\n" not in response:
                block = conn.recv(65536)
                if not block:
                    break
                response += block
        return bool(json.loads(response.split(b"\n", 1)[0].decode("utf-8")).get("ok"))
    except (OSError, ValueError, KeyError):
        return False


def bridge_is_live(discovery, ping_fn=ping, pid_alive_fn=pid_alive):
    """Live = the advertised pid exists AND the socket answers with the token.

    Both checks matter: a dead pid with a lingering socket is someone else's
    port, a live pid with a dead socket is a FreeCAD whose bridge stopped.
    """
    return bool(discovery) and pid_alive_fn(discovery.get("pid")) and ping_fn(discovery)


# --------------------------------------------------------------------------- launching

def find_appimage(environ=os.environ):
    """FreeCAD AppImage: $AGENTSMITH_FREECAD, then common dirs, then $PATH."""
    override = environ.get(APPIMAGE_ENV)
    if override:
        if os.path.isfile(override):
            return override
        raise SystemExit("%s=%r does not exist" % (APPIMAGE_ENV, override))
    candidates = []
    for directory in APPIMAGE_SEARCH_DIRS:
        directory = os.path.expanduser(directory)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if "freecad" in name.lower() and name.lower().endswith(".appimage"):
                candidates.append(os.path.join(directory, name))
    if candidates:
        return sorted(candidates)[-1]  # newest by name: AppImages carry their version
    import shutil
    found = shutil.which("freecad")
    if found:
        return found
    raise SystemExit("No FreeCAD AppImage found (searched %s and $PATH). "
                     "Set %s=/path/to/freecad.appimage" %
                     (", ".join(APPIMAGE_SEARCH_DIRS), APPIMAGE_ENV))


def acquire_lock(lock_file=LOCK_FILE, pid_alive_fn=pid_alive):
    """One launcher at a time. A lock whose pid is dead is stale and is taken over."""
    for _ in range(2):
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock_file, "r", encoding="ascii") as handle:
                    holder = int(handle.read().strip() or 0)
            except (OSError, ValueError):
                holder = 0
            if pid_alive_fn(holder):
                return False
            os.unlink(lock_file)  # stale: holder died between launch and cleanup
    return False


def release_lock(lock_file=LOCK_FILE):
    try:
        os.unlink(lock_file)
    except OSError:
        pass


def launch(appimage, timeout=READY_TIMEOUT):
    """Launch FreeCAD with this file as its startup script; wait until the bridge
    answers. Returns the discovery record. The launched process is detached — it
    must outlive this launcher."""
    stale = read_discovery()
    with open(LOG_FILE, "ab") as log:
        process = subprocess.Popen([appimage, os.path.abspath(__file__)],
                                   stdout=log, stderr=log, start_new_session=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump({"launcher_child_pid": process.pid, "appimage": appimage}, handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("FreeCAD exited with code %s before the bridge came up "
                             "— see %s" % (process.returncode, LOG_FILE))
        discovery = read_discovery()
        # The record must be OURS, not the stale one: a new pid, and answering.
        if discovery and discovery != stale and bridge_is_live(discovery):
            return discovery
        time.sleep(2)
    raise SystemExit("Bridge did not come up within %d s. FreeCAD may still be "
                     "booting — check again with --status BEFORE launching anything "
                     "else; a second launch is exactly the failure mode this tool "
                     "exists to prevent. Log: %s" % (timeout, LOG_FILE))


def stop():
    """Stop the FreeCAD this tool launched. Refuses to kill an instance it cannot
    positively identify as the current bridge host."""
    discovery = read_discovery()
    state = read_discovery(STATE_FILE) or {}
    stopped = []
    if discovery and bridge_is_live(discovery):
        os.kill(discovery["pid"], 15)
        stopped.append(discovery["pid"])
    child = state.get("launcher_child_pid")
    if pid_alive(child):
        try:
            os.kill(child, 15)
            stopped.append(child)
        except OSError:
            pass
    if stopped:
        time.sleep(3)
        for pid in stopped:
            if pid_alive(pid):
                os.kill(pid, 9)
    try:
        os.unlink(STATE_FILE)
    except OSError:
        pass
    return stopped


# --------------------------------------------------------------------------- FreeCAD boot

def freecad_boot():
    """Runs inside FreeCAD (passed as a startup script): bring the bridge up."""
    import FreeCAD as App  # noqa: F401 — proves we are inside FreeCAD
    from PySide import QtCore

    def go():
        import traceback
        try:
            import BridgeGui
            # run_e2e refuses to start without an active document: the bridge
            # attaches its transaction to one and a fresh FreeCAD has none.
            App.newDocument("EvalHost")
            panel = BridgeGui.BridgePanel()
            panel.show()
            panel.access.setCurrentIndex(2)  # Edit + Python: what run_e2e needs
            panel.server.start()             # falls back to an ephemeral port itself
            globals()["_keepalive"] = panel  # keep it out of the garbage collector
            print("host_bridge: READY, discovery %s" % DISCOVERY_FILE, flush=True)
        except Exception:
            print("host_bridge: FAILED\n" + traceback.format_exc(), flush=True)

    # Delayed so the workbench registry and event loop are fully up first.
    QtCore.QTimer.singleShot(2500, go)


# --------------------------------------------------------------------------- CLI

def main(argv):
    action = argv[1] if len(argv) > 1 else "ensure"
    discovery = read_discovery()
    live = bridge_is_live(discovery)

    if action == "--status":
        if live:
            print("live: pid %s, port %s, version %s" %
                  (discovery["pid"], discovery["port"], discovery.get("version")))
            return 0
        print("not live" + (" (stale discovery: pid %s)" % discovery.get("pid")
                            if discovery else " (no discovery file)"))
        return 3

    if action == "--stop":
        stopped = stop()
        print("stopped: %s" % (stopped or "nothing to stop"))
        return 0

    if action in ("ensure", "--replace"):
        if action == "--replace" and live:
            print("replacing pid %s" % discovery["pid"])
            stop()
            live = False
        if live:
            print("reusing live bridge: pid %s, port %s — NOT launching another "
                  "FreeCAD" % (discovery["pid"], discovery["port"]))
            return 0
        if not acquire_lock():
            print("another launcher already holds %s — wait for it instead of "
                  "launching a second FreeCAD" % LOCK_FILE)
            return 4
        try:
            fresh = launch(find_appimage())
            print("launched: pid %s, port %s" % (fresh["pid"], fresh["port"]))
            return 0
        finally:
            release_lock()

    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        import FreeCAD  # noqa: F401
        _inside_freecad = True
    except ImportError:
        _inside_freecad = False
    if _inside_freecad:
        freecad_boot()
    else:
        raise SystemExit(main(sys.argv))
