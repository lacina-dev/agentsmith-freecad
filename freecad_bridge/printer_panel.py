"""printer_panel.py -- the Printers section of the AgentSmith chat panel.

Split out of BridgeGui.py: printer discovery, the remembered list and the
background monitor are a self-contained feature with their own state machine, and
keeping them in the 4000-line panel module made both harder to follow.

The logic worth testing lives in printer_discovery.py and printer_book.py; what
remains here is Qt — a worker thread so the interface never blocks on the
network, and a widget that renders four independent facts about each machine
without collapsing them into one misleading lamp.
"""

import os
import time

from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:  # FreeCAD versions exposing widgets through QtGui
    QtWidgets = QtGui

import printer_book
import printer_discovery


#: How often the remembered printers are re-checked in the background.
MONITOR_INTERVAL_MS = 30000


class PrinterScanWorker(QtCore.QThread):
    """Look for printers off the GUI thread.

    Discovery takes seconds (mDNS rounds) to tens of seconds (a subnet probe),
    and FreeCAD's interface must stay responsive throughout -- a modeller who
    cannot rotate the view while the panel looks for a printer will simply stop
    using the panel.
    """

    found = QtCore.Signal(list)
    progressed = QtCore.Signal(int, int)
    failed = QtCore.Signal(str)

    def __init__(self, deep=False, parent=None, known_only=False):
        super(PrinterScanWorker, self).__init__(parent)
        self.deep = deep
        # known_only skips discovery entirely and just asks the machines we
        # already know about. That is the startup case: the list is on disk, only
        # the current state is missing, and mDNS rounds would add seconds for
        # nothing.
        self.known_only = known_only
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            config = printer_discovery.load_config()
            self.book = printer_book.load_book()

            # Ask what we already know before looking for anything new: printers
            # the user has remembered, then printers the config names. Waiting for
            # a known machine to advertise itself reports it as offline whenever
            # it does not announce -- the default for Moonraker.
            entries = printer_book.book_entries(self.book)
            known = {e.get("address") for e in entries}
            sources = [printer_discovery.configured_entries(config)]
            if not self.known_only:
                sources.append(printer_discovery.discover_mdns())
                sources.append(printer_discovery.discover_bambu(2.0))
            for source in sources:
                entries += [e for e in source if e.get("address") not in known]
                known = {e.get("address") for e in entries}

            if self.deep and not self._cancelled:
                extra = printer_discovery.scan_subnet(
                    progress=lambda done, total: (
                        self.progressed.emit(done, total) or not self._cancelled))
                entries += [e for e in extra if e.get("address") not in known]

            # Asked in parallel: an offline printer costs an HTTP timeout per
            # dialect tried, so the cost is paid by machines that are switched
            # off, not by the ones answering. Measured with three live and three
            # dead printers: 6.3 s concurrent against 18.4 s one at a time.
            from concurrent.futures import ThreadPoolExecutor

            def ask(entry):
                if self._cancelled:
                    return None
                key = printer_discovery.api_key_for(config, entry.get("address"))
                try:
                    return printer_discovery.identify(entry, key)
                except Exception:
                    return dict(entry, state=printer_discovery.PrinterState.UNREACHABLE)

            with ThreadPoolExecutor(max_workers=min(8, max(1, len(entries)))) as pool:
                identified = [r for r in pool.map(ask, entries) if r]

            rows = printer_book.annotate(self.book, identified)
            network = printer_book.current_network()
            # A printer meeting us for the first time gets the friendly label from
            # the config ("Prusa CORE One") rather than the hostname it reports
            # ("prusa-core-one"). Only for new entries -- an existing name is the
            # user's and is never touched.
            config_labels = {printer_discovery.config_signature(p): p.get("label")
                             for p in (config.get("printers") or {}).values()}
            seen = set()
            for row in rows:
                if row.get("kind_verified") and row.get("identity") != "mismatch":
                    # Only machines that proved what they are get written down;
                    # remembering a guess would make the guess permanent.
                    seen_before = printer_book.find(
                        self.book, printer_book.entry_id(row)) is not None
                    record = printer_book.remember(
                        self.book, dict(row, network=network),
                        name=None if seen_before
                        else config_labels.get(row.get("address")))
                    seen.add(record["id"])
                elif row.get("book_id"):
                    seen.add(row["book_id"])
            merged_preview = printer_discovery.merge_with_config(config, rows)
            for row in merged_preview:
                if row.get("book_id"):
                    printer_book.record_state(self.book, row["book_id"],
                                              row.get("state"), row.get("can_slice"))
            printer_book.save_book(self.book)

            merged = merged_preview
            merged += printer_book.unseen_rows(self.book, seen)
            self.found.emit(merged)
        except Exception as exc:
            self.failed.emit(str(exc))


class PrinterListWidget(QtWidgets.QGroupBox):
    """Which printers exist, and what is actually true about each right now.

    Four columns because they are four independent facts. "Reachable" is not
    "usable": a machine can answer on the network and still be unprintable
    because we hold no credential for it or have no slicer profile. One green
    lamp would hide exactly the distinctions that decide whether a print can be
    started.
    """

    STATE_COLOURS = {
        printer_discovery.PrinterState.CHECKING: "#0277bd",
        printer_discovery.PrinterState.CONNECTED: "#2e7d32",
        printer_discovery.PrinterState.RESPONDING: "#ef6c00",
        printer_discovery.PrinterState.FOUND: "#757575",
        printer_discovery.PrinterState.UNREACHABLE: "#9e9e9e",
    }

    def __init__(self, parent=None):
        super(PrinterListWidget, self).__init__("Printers", parent)
        self.worker = None

        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(["Printer", "Address", "State", "Can slice"])
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(110)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemDoubleClicked.connect(lambda item, _c: self._rename(item))
        header = self.table.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)

        self.refresh_button = QtWidgets.QPushButton("Search")
        self.refresh_button.clicked.connect(lambda: self.refresh(False))
        self.add_button = QtWidgets.QPushButton("Add by IP…")
        self.add_button.setToolTip(
            "Manual entry for networks where a printer does not announce itself.\n"
            "A switched-off printer can be added too — it stays unreachable until it answers.")
        self.add_button.clicked.connect(self._add_by_address)
        # Off by default and spelled out in the tooltip: knocking on all 254
        # addresses is a port scan, which is unremarkable at home and a reportable
        # event on a managed network. The user opts in knowingly or not at all.
        self.deep_scan = QtWidgets.QCheckBox("also scan the network")
        self.deep_scan.setToolTip(
            "Unchecked: only listens for machines that announce themselves (mDNS) —\n"
            "nothing is sent to any individual machine.\n\n"
            "Checked: a connection is tried against every address on the local network.\n"
            "Harmless at home; on a corporate network it can look like a port scan.")
        self.status = QtWidgets.QLabel("not searched")
        self.status.setStyleSheet("color: #888;")

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.add_button)
        controls.addWidget(self.deep_scan)
        controls.addWidget(self.status, 1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)

        # The remembered printers are on disk, so they appear immediately -- the
        # panel opening empty and waiting for a button press wasted the whole
        # point of remembering them. Their live state is then filled in by a
        # background pass over the addresses we already know; discovery proper
        # stays behind the button.
        self._show(printer_book.cached_rows(printer_book.load_book()))
        QtCore.QTimer.singleShot(300, self._check_known)

        # Keep the states current on their own. Only the addresses we already
        # know are asked -- no discovery, no scanning -- so a tick is a handful
        # of short HTTP requests. It runs quietly: no status flicker, no lost
        # selection, and it steps aside for anything the user started.
        self.monitor_timer = QtCore.QTimer(self)
        self.monitor_timer.setInterval(MONITOR_INTERVAL_MS)
        self.monitor_timer.timeout.connect(self._monitor_tick)
        self.monitor_timer.start()

    def _check_known(self):
        """Resolve the state of printers we already know, without discovery."""
        self.refresh(False, known_only=True)

    def _monitor_tick(self):
        """One background round of state checking.

        Skipped whenever something the user started is already running, and
        whenever the panel is not on screen -- polling a hidden widget is pure
        waste, and the first thing shown after it reappears is a fresh check.
        """
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.isVisible():
            return
        self.refresh(False, known_only=True, quiet=True)

    def refresh(self, deep=None, known_only=False, quiet=False):
        if self.worker and self.worker.isRunning():
            if quiet:
                return                     # a background tick never interrupts
            self.worker.cancel()
            self.status.setText("cancelling…")
            return
        deep = self.deep_scan.isChecked() if deep is None else deep
        self.quiet_run = quiet
        self.worker = PrinterScanWorker(deep, self, known_only=known_only)
        self.worker.found.connect(self._show)
        self.worker.failed.connect(self._failed)
        self.worker.progressed.connect(self._progress)
        if not quiet:
            self.worker.finished.connect(
                lambda: self.refresh_button.setText("Search"))
            self.refresh_button.setText("Cancel")
            self.status.setText("checking state…" if known_only
                                else ("scanning the network…" if deep else "searching…"))
        self.worker.start()

    def _selected_book_id(self):
        item = self.table.currentItem()
        return item.data(0, QtCore.Qt.UserRole) if item else None

    def _add_by_address(self):
        """Add a printer by typing its address -- the escape hatch for every
        network where discovery cannot work: segmented VLANs, a printer on a
        different subnet, or one that is simply switched off right now."""
        address, ok = QtWidgets.QInputDialog.getText(
            self, "Add a printer", "IP address or hostname (optionally :port):")
        if not ok or not address.strip():
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Printer name", "What do you want to call it?", text=address.strip())
        if not ok:
            return
        host, _, port = address.strip().partition(":")
        try:
            data = printer_book.load_book()
            printer_book.add_manual(data, host.strip(), name=name.strip() or None,
                                    port=int(port) if port.strip().isdigit() else None)
            printer_book.save_book(data)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Failed", str(exc))
            return
        self.refresh(False)

    def _rename(self, item=None):
        printer_id = (item.data(0, QtCore.Qt.UserRole) if item
                      else self._selected_book_id())
        if not printer_id:
            return
        current = item.text(0) if item else ""
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename", "Printer name:", text=current)
        if not ok:
            return
        data = printer_book.load_book()
        if printer_book.rename(data, printer_id, name):
            printer_book.save_book(data)
            self._reload_names()

    def _forget(self):
        printer_id = self._selected_book_id()
        if not printer_id:
            return
        item = self.table.currentItem()
        confirm = QtWidgets.QMessageBox.question(
            self, "Forget printer",
            "Remove \u201c%s\u201d from the list?\n\nThe printer itself is untouched; "
            "it reappears on the next search." % item.text(0),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        data = printer_book.load_book()
        if printer_book.forget(data, printer_id):
            printer_book.save_book(data)
            index = self.table.indexOfTopLevelItem(item)
            self.table.takeTopLevelItem(index)

    def _add_to_config(self, item):
        """Give a discovered printer a config entry so it can be sliced for."""
        row = item.data(1, QtCore.Qt.UserRole) or {}
        confirm = QtWidgets.QMessageBox.question(
            self, "Add to the config",
            "Write \u201c%s\u201d (%s) into slicer-config.json?\n\n"
            "Only the network part is filled in. Slicer profiles stay empty — "
            "inventing them would mean producing G-code for a machine that never "
            "had them. Until they are added it stays at \u201cCan slice: no\u201d."
            % (item.text(0), row.get("address")),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        try:
            key, _block = printer_discovery.add_to_config(row, label=item.text(0))
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Failed", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, "Added",
            "Written as \u201c%s\u201d. Fill in the profiles and, if needed, api_key_file, "
            "and it can be sliced for." % key)
        self.refresh(False)

    def _reload_names(self):
        """Repaint names from the book without re-running discovery."""
        data = printer_book.load_book()
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            printer = printer_book.find(data, item.data(0, QtCore.Qt.UserRole) or "")
            if printer and printer.get("name"):
                item.setText(0, printer["name"])

    def _context_menu(self, position):
        item = self.table.itemAt(position)
        if item is None or not item.data(0, QtCore.Qt.UserRole):
            return
        self.table.setCurrentItem(item)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Rename…", lambda: self._rename(item))
        row = item.data(1, QtCore.Qt.UserRole) or {}
        if not row.get("configured") and row.get("kind") not in (None, "bambu"):
            menu.addAction("Add to the config…", lambda: self._add_to_config(item))
        menu.addAction("Forget", self._forget)
        menu.exec_(self.table.viewport().mapToGlobal(position))

    def _progress(self, done, total):
        self.status.setText("scanning the network… %d/%d" % (done, total))

    def _remember_selection(self):
        item = self.table.currentItem()
        scroll = self.table.verticalScrollBar().value() if self.table else 0
        return (item.data(0, QtCore.Qt.UserRole) if item else None,
                item.text(1) if item else None, scroll)

    def _restore_selection(self, saved):
        """Put the cursor back where the user left it after a silent redraw."""
        book_id, address, scroll = saved
        if book_id or address:
            for index in range(self.table.topLevelItemCount()):
                item = self.table.topLevelItem(index)
                if (book_id and item.data(0, QtCore.Qt.UserRole) == book_id) or \
                        (not book_id and item.text(1) == address):
                    self.table.setCurrentItem(item)
                    break
        self.table.verticalScrollBar().setValue(scroll)

    def _failed(self, message):
        self.status.setText("search failed: %s" % message)

    def _show(self, rows):
        saved = self._remember_selection()
        self.table.clear()
        printers = [r for r in rows
                    if r.get("kind") or r.get("configured") or r.get("remembered")]
        for row in printers:
            item = QtWidgets.QTreeWidgetItem([
                row.get("label") or row.get("address") or "?",
                row.get("address") or "—",
                printer_discovery.STATE_LABELS.get(row.get("state"), "?"),
                "yes" if row.get("can_slice") else "no",
            ])
            item.setData(0, QtCore.Qt.UserRole, row.get("book_id"))
            item.setData(1, QtCore.Qt.UserRole, row)
            colour = self.STATE_COLOURS.get(row.get("state"))
            if colour:
                item.setForeground(2, QtGui.QBrush(QtGui.QColor(colour)))
            if not row.get("can_slice"):
                item.setForeground(3, QtGui.QBrush(QtGui.QColor("#9e9e9e")))
            hints = []
            if row.get("identity") == "mismatch":
                # Something answers here, but not our printer. Say so loudly:
                # the alternative is a Start button aimed at a stranger's device.
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#c62828")))
                hints.append("WARNING: " + row.get("note", "a different device"))
            if row.get("remembered") and row.get("last_seen"):
                hints.append("last seen %s" % row["last_seen"])
            if not row.get("configured"):
                hints.append("not in the config — add a profile and a key")
            if row.get("note"):
                hints.append(row["note"])
            if row.get("kind"):
                hints.append("protocol: %s" % row["kind"])
            item.setToolTip(0, "\n".join(hints))
            self.table.addTopLevelItem(item)
        self._restore_selection(saved)
        strangers = len(rows) - len(printers)
        usable = sum(1 for r in printers
                     if r.get("state") == printer_discovery.PrinterState.CONNECTED
                     and r.get("can_slice"))
        summary = "%d printers, %d ready to print" % (len(printers), usable)
        if strangers:
            summary += ", %d other devices skipped" % strangers
        self.status.setText(summary)
