"""printer_panel.py -- the Tiskárny section of the AgentSmith chat panel.

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
        super(PrinterListWidget, self).__init__("Tiskárny", parent)
        self.worker = None

        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(["Tiskárna", "Adresa", "Stav", "Umím slicovat"])
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

        self.refresh_button = QtWidgets.QPushButton("Hledat")
        self.refresh_button.clicked.connect(lambda: self.refresh(False))
        self.add_button = QtWidgets.QPushButton("Přidat podle IP…")
        self.add_button.setToolTip(
            "Ruční přidání pro sítě, kde se tiskárna sama neohlásí.\n"
            "Vypnutou tiskárnu lze přidat také — zůstane nedostupná, dokud se neozve.")
        self.add_button.clicked.connect(self._add_by_address)
        # Off by default and spelled out in the tooltip: knocking on all 254
        # addresses is a port scan, which is unremarkable at home and a reportable
        # event on a managed network. The user opts in knowingly or not at all.
        self.deep_scan = QtWidgets.QCheckBox("i aktivní sken sítě")
        self.deep_scan.setToolTip(
            "Bez zaškrtnutí se jen naslouchá tomu, co se samo ohlásí (mDNS) —\n"
            "nic se neposílá na konkrétní stroje.\n\n"
            "Zaškrtnuté: zkusí se spojení na každou adresu v lokální síti.\n"
            "Doma neškodné, ve firemní síti to může vypadat jako skenování portů.")
        self.status = QtWidgets.QLabel("nehledáno")
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
            self.status.setText("ruším…")
            return
        deep = self.deep_scan.isChecked() if deep is None else deep
        self.quiet_run = quiet
        self.worker = PrinterScanWorker(deep, self, known_only=known_only)
        self.worker.found.connect(self._show)
        self.worker.failed.connect(self._failed)
        self.worker.progressed.connect(self._progress)
        if not quiet:
            self.worker.finished.connect(
                lambda: self.refresh_button.setText("Hledat"))
            self.refresh_button.setText("Zrušit")
            self.status.setText("zjišťuji stav…" if known_only
                                else ("skenuji síť…" if deep else "hledám…"))
        self.worker.start()

    def _selected_book_id(self):
        item = self.table.currentItem()
        return item.data(0, QtCore.Qt.UserRole) if item else None

    def _add_by_address(self):
        """Add a printer by typing its address -- the escape hatch for every
        network where discovery cannot work: segmented VLANs, a printer on a
        different subnet, or one that is simply switched off right now."""
        address, ok = QtWidgets.QInputDialog.getText(
            self, "Přidat tiskárnu", "IP adresa nebo hostname (volitelně :port):")
        if not ok or not address.strip():
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Název tiskárny", "Jak jí chceš říkat?", text=address.strip())
        if not ok:
            return
        host, _, port = address.strip().partition(":")
        try:
            data = printer_book.load_book()
            printer_book.add_manual(data, host.strip(), name=name.strip() or None,
                                    port=int(port) if port.strip().isdigit() else None)
            printer_book.save_book(data)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Nepovedlo se", str(exc))
            return
        self.refresh(False)

    def _rename(self, item=None):
        printer_id = (item.data(0, QtCore.Qt.UserRole) if item
                      else self._selected_book_id())
        if not printer_id:
            return
        current = item.text(0) if item else ""
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Přejmenovat", "Název tiskárny:", text=current)
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
            self, "Zapomenout tiskárnu",
            "Odstranit „%s\u201c ze seznamu?\n\nTiskárny se to nijak nedotkne; "
            "znovu se objeví při dalším hledání." % item.text(0),
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
            self, "Přidat do konfigurace",
            "Zapsat „%s\u201c (%s) do slicer-config.json?\n\n"
            "Doplní se jen síťová část. Slicovací profily zůstanou prázdné — "
            "vymýšlet je by znamenalo vyrobit G-code pro stroj, který je nikdy "
            "neměl. Do té doby zůstane u „Umím slicovat: ne\u201c."
            % (item.text(0), row.get("address")),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        try:
            key, _block = printer_discovery.add_to_config(row, label=item.text(0))
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Nepovedlo se", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, "Přidáno",
            "Zapsáno jako „%s\u201c. Doplň profily a případně api_key_file, "
            "pak půjde slicovat." % key)
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
        menu.addAction("Přejmenovat…", lambda: self._rename(item))
        row = item.data(1, QtCore.Qt.UserRole) or {}
        if not row.get("configured") and row.get("kind") not in (None, "bambu"):
            menu.addAction("Přidat do konfigurace…", lambda: self._add_to_config(item))
        menu.addAction("Zapomenout", self._forget)
        menu.exec_(self.table.viewport().mapToGlobal(position))

    def _progress(self, done, total):
        self.status.setText("skenuji síť… %d/%d" % (done, total))

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
        self.status.setText("hledání selhalo: %s" % message)

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
                "ano" if row.get("can_slice") else "ne",
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
                hints.append("POZOR: " + row.get("note", "jiné zařízení"))
            if row.get("remembered") and row.get("last_seen"):
                hints.append("naposledy viděna %s" % row["last_seen"])
            if not row.get("configured"):
                hints.append("není v konfiguraci — doplň profil a klíč")
            if row.get("note"):
                hints.append(row["note"])
            if row.get("kind"):
                hints.append("protokol: %s" % row["kind"])
            item.setToolTip(0, "\n".join(hints))
            self.table.addTopLevelItem(item)
        self._restore_selection(saved)
        strangers = len(rows) - len(printers)
        usable = sum(1 for r in printers
                     if r.get("state") == printer_discovery.PrinterState.CONNECTED
                     and r.get("can_slice"))
        summary = "%d tiskáren, %d připraveno k tisku" % (len(printers), usable)
        if strangers:
            summary += ", %d jiných zařízení přeskočeno" % strangers
        self.status.setText(summary)
