"""User-confirmed, local loot accounting UI. Never sends game input."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import uuid

from loot_ledger import LootLedger, MAX_INTEGER
from loot_prices import price_suggestion
from loot_reader import parse_item_text, read_item_image


def integer(text, label, *, unknown=False, signed=False):
    text = str(text).strip()
    if text in ("", "?") and unknown:
        return None
    pattern = r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)" if signed else r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)"
    if not re.fullmatch(pattern, text) or len(text) > 30:
        raise ValueError(f"{label} needs a whole number" + (" or ? for unknown" if unknown else ""))
    value = int(text.replace(",", ""))
    if abs(value) > MAX_INTEGER:
        raise ValueError(f"{label} is too large")
    return value


def shown(value):
    if value is None:
        return "?"
    if isinstance(value, Decimal):
        text = format(value, ",f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return f"{value:,}"


def local_time(timestamp):
    if timestamp is None:
        return "No snapshot"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError, TypeError):
        return f"Unrenderable time ({timestamp})"


def _atomic_bytes(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@dataclass(frozen=True)
class ReadingEvidence:
    identity: str
    session_id: str
    result: object
    path: Path


class LootController:
    """Parse UI entries and keep confirmation/evidence rules outside Tk."""

    def __init__(self, path):
        self.ledger = LootLedger(Path(path))
        self.evidence_dir = Path(path).parent / "loot-evidence"

    def session(self):
        session = self.ledger.active_session
        if session is None:
            raise ValueError("Start an outing first")
        return session

    def set_item(self, item_id, name, kind, price):
        name = name.strip()
        if any(key != item_id and item["name"].casefold() == name.casefold()
               for key, item in self.ledger.catalog.items()):
            raise ValueError("Use a unique item name so readings can identify it")
        price = str(price).strip()
        if price in ("", "?"):
            value = None
        else:
            try:
                value = Decimal(price)
            except InvalidOperation:
                raise ValueError("Unit price needs a number or ? for unknown") from None
        self.ledger.set_item(item_id, name, kind, value)

    @staticmethod
    def _confirmed(confirmed):
        if confirmed is not True:
            raise ValueError("Confirm the values before saving")

    @staticmethod
    def _counts(values, catalog):
        if any(key not in catalog for key in values):
            raise ValueError("An entered item is outside this outing's catalog")
        return {key: integer(values.get(key, "?"), item["name"], unknown=True)
                for key, item in catalog.items()}

    def start(self, name, wallet, counts, *, confirmed=False, timestamp=None):
        self._confirmed(confirmed)
        return self.ledger.start_session(name.strip(), integer(wallet, "Opening wallet"),
                                         self._counts(counts, self.ledger.catalog), timestamp)

    def record(self, kind, item_id, quantity, mesos, note="", *, event_id=None):
        return self.ledger.record_event(event_id or str(uuid.uuid4()), kind, item_id,
            integer(quantity, "Quantity", signed=kind == "adjustment"),
            integer(mesos, "Mesos", signed=kind in ("transfer", "adjustment")), note)

    def _check_evidence(self, evidence, mode):
        if evidence.session_id != self.session()["id"]:
            raise ValueError("This reading belongs to an earlier outing. Read or enter new evidence.")
        if evidence.result.mode != mode:
            raise ValueError("Inventory readings belong in snapshots; pickup readings belong in the journal")

    def proposed_counts(self, evidence):
        catalog = self.session()["catalog"]
        names = {}
        for item_id, item in catalog.items():
            names.setdefault(item["name"], []).append(item_id)
        return {names[item.name][0]: str(item.quantity) for item in evidence.result.items
                if len(names.get(item.name, [])) == 1}

    def snapshot_defaults(self, evidence=None):
        counts = dict.fromkeys(self.session()["catalog"], "?")
        if evidence is not None:
            self._check_evidence(evidence, "inventory")
            counts.update(self.proposed_counts(evidence))
        return counts

    def snapshot(self, wallet, counts, *, confirmed=False, evidence=None, timestamp=None):
        self._confirmed(confirmed)
        if evidence is not None:
            self._check_evidence(evidence, "inventory")
        identity = f"reading:{evidence.identity}:{uuid.uuid4().hex}" if evidence else str(uuid.uuid4())
        return self.ledger.snapshot(identity, integer(wallet, "Wallet", unknown=True),
                                     self._counts(counts, self.session()["catalog"]), timestamp)

    def prepare_read(self, result):
        session_id = self.session()["id"]
        source = b"image\0" + result.source_png if result.source_png is not None else b"text\0" + result.raw_text.encode("utf-8")
        identity = hashlib.sha256(session_id.encode() + b"\0" + result.mode.encode() + b"\0" + source).hexdigest()
        payload = {"session_id": session_id, "identity": identity, "mode": result.mode,
                   "raw_text": result.raw_text, "warnings": list(result.warnings),
                   "items": [asdict(item) for item in result.items], "ocr_texts": list(result.ocr_texts),
                   "conflicted_names": list(result.conflicted_names), "review_required": True}
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        reading_hash = hashlib.sha256(content).hexdigest()[:16]
        path = self.evidence_dir / f"{identity}-{reading_hash}.json"
        if result.source_png is not None:
            image_path = self.evidence_dir / f"{identity}.png"
            if not image_path.exists():
                _atomic_bytes(image_path, result.source_png)
        if not path.exists():
            _atomic_bytes(path, content)
        return ReadingEvidence(identity, session_id, result, path)

    def confirm_pickups(self, evidence, counts, *, confirmed=False):
        self._confirmed(confirmed)
        self._check_evidence(evidence, "pickup")
        proposals = self.proposed_counts(evidence)
        if any(key not in proposals for key in counts):
            raise ValueError("Record unrecognized items with a manual entry")
        parsed = {key: integer(value, "Pickup quantity", unknown=True) for key, value in counts.items()}
        if any(value == 0 for value in parsed.values()):
            raise ValueError("Pickup quantity must be positive; use ? to skip a proposal")
        pending = []
        existing = {row["id"]: row for row in self.session()["records"]}
        note = f"Confirmed local evidence {evidence.identity}"
        for item_id, quantity in parsed.items():
            if quantity is None:
                continue
            item_hash = hashlib.sha256(item_id.encode()).hexdigest()[:16]
            event_id = f"reading:{evidence.identity}:{item_hash}"
            previous = existing.get(event_id)
            if previous is not None and any(previous.get(key) != value for key, value in
                    {"type": "event", "kind": "pickup", "item_id": item_id,
                     "quantity": quantity, "mesos": 0, "note": note}.items()):
                raise ValueError("This source item was already confirmed with different values. Use a manual adjustment to preserve the correction.")
            pending.append((event_id, item_id, quantity))
        added = 0
        for event_id, item_id, quantity in pending:
            added += self.ledger.record_event(event_id, "pickup", item_id, quantity, 0, note)
        return added


class ScrollForm(ttk.Frame):
    def __init__(self, parent, height=260):
        super().__init__(parent)
        canvas = tk.Canvas(self, height=height, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(canvas, padding=5)
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))


class Dialog:
    def __init__(self, owner, title):
        self.owner = owner
        self.window = tk.Toplevel(owner.window)
        self.window.title(title)
        self.window.transient(owner.window)
        self.window.minsize(580, 360)
        self.body = ttk.Frame(self.window, padding=14)
        self.body.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(self.window)
        ttk.Label(self.window, textvariable=self.status_var, foreground="#9b2525", wraplength=680,
                  padding=10).pack(fill="x", side="bottom")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())
        owner.dialogs.append(self)
        self.window.bind("<Destroy>", self.destroyed, add="+")

    def destroyed(self, event):
        if event.widget == self.window and self in self.owner.dialogs:
            self.owner.dialogs.remove(self)

    def close(self):
        self.window.destroy()

    def error(self, error):
        self.status_var.set(str(error))
        self.owner.refresh()


class SnapshotDialog(Dialog):
    def __init__(self, owner, *, opening=False, evidence=None):
        self.opening, self.evidence = opening, evidence
        session = owner.controller.ledger.active_session if opening else owner.controller.session()
        self.catalog = owner.controller.ledger.catalog if opening else session["catalog"]
        self.session_id = None if session is None else session["id"]
        defaults = dict.fromkeys(self.catalog, "?") if opening else owner.controller.snapshot_defaults(evidence)
        try:
            suggestion = owner.wallet() if owner.wallet is not None else None
        except Exception:
            suggestion = None
        super().__init__(owner, "Start an outing" if opening else "Inventory and wallet snapshot")
        self.name_var = tk.StringVar(self.window, value=datetime.now().strftime("Outing %Y-%m-%d %H:%M"))
        if opening:
            ttk.Label(self.body, text="Outing name").pack(anchor="w")
            ttk.Entry(self.body, textvariable=self.name_var).pack(fill="x", pady=(0, 8))
        self.wallet_var = tk.StringVar(self.window, value="?" if suggestion is None else str(suggestion))
        ttk.Label(self.body, text="Wallet mesos at the checkpoint").pack(anchor="w")
        ttk.Entry(self.body, textvariable=self.wallet_var).pack(fill="x")
        ttk.Label(self.body, text="The overlay wallet is only a suggestion. Its age is unknown and it may be stale.\n"
                  "Check the wallet against the item counts at the same time.", foreground="#9b2525",
                  wraplength=650).pack(anchor="w", pady=6)
        self.initial_timestamp = time.time()
        self.initial_time_text = local_time(self.initial_timestamp)
        self.time_var = tk.StringVar(self.window, value=self.initial_time_text)
        timing = ttk.Frame(self.body)
        timing.pack(fill="x")
        ttk.Label(timing, text="Checkpoint local time").pack(side="left")
        ttk.Entry(timing, textvariable=self.time_var, width=23).pack(side="left", padx=8)
        ttk.Label(self.body, text="Enter individual item units, including individual stars. Blank or ? means unknown.\n"
                  "Enter 0 only when you confirmed none. Prices below are per item unit.", wraplength=650).pack(anchor="w", pady=8)
        form = ScrollForm(self.body)
        form.pack(fill="both", expand=True)
        self.count_vars = {}
        for row, (item_id, item) in enumerate(self.catalog.items()):
            ttk.Label(form.body, text=item["name"]).grid(row=row, column=0, sticky="w", pady=3)
            variable = self.count_vars[item_id] = tk.StringVar(self.window, value=defaults[item_id])
            ttk.Entry(form.body, textvariable=variable, width=15).grid(row=row, column=1, padx=10, pady=3)
            ttk.Label(form.body, text=f"{shown(item['unit_value'])} mesos / unit").grid(row=row, column=2, sticky="w")
        form.body.columnconfigure(0, weight=1)
        if opening:
            ttk.Label(self.body, text="Set prices/custom items in Catalog before starting. Each outing keeps its prices.",
                      wraplength=650).pack(anchor="w", pady=7)
            if session is not None:
                ttk.Label(self.body, text=f"Starting this outing closes '{session['name']}' for new entries. Its history remains saved.",
                          wraplength=650).pack(anchor="w", pady=5)
        elif evidence:
            ttk.Label(self.body, text="Proposals are prefilled. All other counts remain unknown. Check the image/text age;\n"
                      "enter the actual checkpoint time and a wallet from that same time.", wraplength=650).pack(anchor="w", pady=7)
        self.confirm_var = tk.BooleanVar(self.window, value=False)
        ttk.Checkbutton(self.body, variable=self.confirm_var,
                        text="I checked the entered wallet/counts and they describe the same checkpoint.").pack(anchor="w", pady=10)
        ttk.Button(self.body, text="Start outing" if opening else "Save confirmed snapshot", command=self.submit).pack(anchor="e")

    def submit(self):
        try:
            if self.opening and self.catalog != self.owner.controller.ledger.catalog:
                raise ValueError("The catalog changed after this form opened. Close it and review a new outing form.")
            entered_time = self.time_var.get().strip()
            timestamp = self.initial_timestamp if entered_time == self.initial_time_text else datetime.strptime(
                entered_time, "%Y-%m-%d %H:%M:%S").timestamp()
            # The default time is displayed to seconds; preserve ordering for rapid entries.
            now = time.time()
            if timestamp > now + 60:
                raise ValueError("Checkpoint time cannot be in the future")
            session = self.owner.controller.ledger.active_session
            if self.session_id != (None if session is None else session["id"]):
                raise ValueError("The active outing changed after this form opened. Close it and open a new form.")
            latest = None if session is None else (session["records"][-1]["timestamp"] if session["records"] else session["started_at"])
            if latest is not None and int(latest) == int(timestamp):
                timestamp = max(latest, timestamp)
            if latest is not None and timestamp < latest:
                raise ValueError(f"Checkpoint time must be at or after the latest record: {local_time(latest)}")
            counts = {key: variable.get() for key, variable in self.count_vars.items()}
            if self.opening:
                self.owner.controller.start(self.name_var.get(), self.wallet_var.get(), counts,
                    confirmed=self.confirm_var.get(), timestamp=timestamp)
            else:
                self.owner.controller.snapshot(self.wallet_var.get(), counts, confirmed=self.confirm_var.get(),
                                               evidence=self.evidence, timestamp=timestamp)
        except (ValueError, OSError) as error:
            self.error(error)
            return
        self.owner.selected_session = None
        self.owner.refresh()
        self.close()


class EventDialog(Dialog):
    def __init__(self, owner):
        session = owner.controller.session()
        self.session_id = session["id"]
        catalog = session["catalog"]
        super().__init__(owner, "Record confirmed activity")
        self.items = {"Cash only": None, **{f"{item['name']} [{key}]": key for key, item in catalog.items()}}
        self.kind_var = tk.StringVar(self.window, "pickup")
        self.item_var = tk.StringVar(self.window, next(label for label in self.items if label != "Cash only"))
        self.quantity_var = tk.StringVar(self.window, "1")
        self.mesos_var = tk.StringVar(self.window, "0")
        self.note_var = tk.StringVar(self.window)
        for label, variable, choices in (
                ("Activity", self.kind_var, ("pickup", "sale", "purchase", "recharge", "transfer", "adjustment")),
                ("Item", self.item_var, tuple(self.items)),
                ("Item units, individual stars", self.quantity_var, None),
                ("Actual total mesos", self.mesos_var, None), ("Note / correction reason", self.note_var, None)):
            ttk.Label(self.body, text=label).pack(anchor="w", pady=(7, 0))
            field = ttk.Combobox(self.body, textvariable=variable, values=choices, state="readonly") if choices else ttk.Entry(self.body, textvariable=variable)
            field.pack(fill="x")
        ttk.Label(self.body, text="Sales, purchases and recharges use positive actual totals. Transfers use signed cash\n"
                  "inflow/outflow with Cash only and quantity 0. Correct a transfer with a counter-transfer.\n"
                  "Adjustments correct the journal only.\n"
                  "Journal entries do not change snapshot counts or add to snapshot profit.", wraplength=650).pack(anchor="w", pady=12)
        ttk.Button(self.body, text="Save confirmed activity", command=self.submit).pack(anchor="e")

    def submit(self):
        try:
            if self.session_id != self.owner.controller.session()["id"]:
                raise ValueError("This form belongs to an earlier outing")
            self.owner.controller.record(self.kind_var.get(), self.items[self.item_var.get()], self.quantity_var.get(),
                                         self.mesos_var.get(), self.note_var.get())
        except (ValueError, OSError) as error:
            self.error(error)
            return
        self.owner.refresh()
        self.close()


def _load_image(path):
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
        if width * height > 4_000_000 or max(width, height) > 4096:
            raise ValueError("Image is too large. Crop to the visible item area before loading.")
        return np.array(image.convert("RGB"))[:, :, ::-1].copy()


def _capture_region(region):
    import mss
    import numpy as np
    if region["width"] * region["height"] > 4_000_000 or max(region["width"], region["height"]) > 4096:
        raise ValueError("Selected area is too large. Select only the visible item area.")
    with mss.mss() as screen:
        return np.array(screen.grab(region))[:, :, :3].copy()


class ReadDialog(Dialog):
    def __init__(self, owner):
        self.session_id = owner.controller.session()["id"]
        super().__init__(owner, "Read items for review")
        self.window.geometry("760x760")
        self.mode_var = tk.StringVar(self.window, "pickup")
        self.confirm_var = tk.BooleanVar(self.window, False)
        self.evidence = None
        self.review_text = None
        self.source_png = None
        self.image_result = None
        self.count_vars = {}
        self.queue = queue.Queue()
        self.busy = False
        self.hidden = False
        self.closed = False
        self.poll_job = None
        self.capture_job = None
        mode = ttk.Frame(self.body)
        mode.pack(fill="x")
        ttk.Label(mode, text="Reading describes").pack(side="left")
        self.mode_box = ttk.Combobox(mode, textvariable=self.mode_var, values=("pickup", "inventory"), state="readonly", width=16)
        self.mode_box.pack(side="left", padx=8)
        self.mode_box.bind("<<ComboboxSelected>>", lambda event: self.new_pasted_source())
        ttk.Label(self.body, text="Pickup counts mean newly collected items. Inventory counts go to a full snapshot.\n"
                  "Paste explicit lines such as Red Snail Shell x 3. Unknown text stays available for review.\n"
                  "Paste only lines not already confirmed; overlapping text or images are counted again.\n"
                  "Text and selected images are saved locally as evidence; no reading is auto-recorded.", wraplength=710).pack(anchor="w", pady=8)
        self.text = ScrolledText(self.body, height=6, wrap="word")
        self.text.pack(fill="x")
        self.text.bind("<<Modified>>", self.source_edited)
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=8)
        self.read_buttons = []
        for label, command in (("Review pasted text", self.read_text), ("Load screenshot", self.load_image),
                               ("Select screen area", self.select_area), ("New pasted source", self.new_pasted_source)):
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side="left", padx=(0, 6))
            self.read_buttons.append(button)
        if owner.select_region is None:
            self.read_buttons[2].state(["disabled"])
        self.preview_button = ttk.Button(self.body, text="View original source image", command=self.view_source, state="disabled")
        self.preview_button.pack(anchor="w")
        self.warning_box = ScrolledText(self.body, height=5, wrap="word", state="disabled")
        self.warning_box.pack(fill="x", pady=5)
        self.set_warnings("No proposals yet. Manual activity entry is always available.")
        self.proposals = ScrollForm(self.body, height=180)
        self.proposals.pack(fill="both", expand=True)
        actions = ttk.Frame(self.body)
        actions.pack(side="bottom", fill="x", before=mode)
        self.confirm_text = tk.StringVar(self.window, "I reviewed the source and edited quantities. ? skips a pickup proposal.")
        ttk.Checkbutton(actions, variable=self.confirm_var,
                        textvariable=self.confirm_text).pack(anchor="w", pady=10)
        self.confirm_button = ttk.Button(actions, text="Confirm reviewed pickups", command=self.submit, state="disabled")
        self.confirm_button.pack(anchor="e")
        self.poll_job = self.window.after(50, self.poll)

    def clear_review(self):
        self.evidence = None
        self.review_text = None
        self.confirm_var.set(False)
        self.count_vars.clear()
        for child in self.proposals.body.winfo_children():
            child.destroy()
        self.confirm_button.state(["disabled"])
        self.preview_button.state(["disabled"] if self.source_png is None else ["!disabled"])

    def new_pasted_source(self, *, keep_text=False):
        self.source_png = None
        self.image_result = None
        self.clear_review()
        if not keep_text:
            self.text.delete("1.0", "end")
        self.text.edit_modified(False)
        self.status_var.set("Ready for a new source. Paste only lines not already confirmed.")

    def source_edited(self, event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            if self.evidence is not None:
                self.clear_review()
                self.status_var.set("Source text changed. Review the edited text before confirming quantities.")

    def set_warnings(self, text):
        self.warning_box.configure(state="normal")
        self.warning_box.delete("1.0", "end")
        self.warning_box.insert("1.0", text)
        self.warning_box.configure(state="disabled")

    def view_source(self):
        if self.source_png is None:
            return None
        import io
        from PIL import Image, ImageTk
        dialog = Dialog(self.owner, "Original source image, scroll to inspect")
        dialog.window.geometry("900x620")
        with Image.open(io.BytesIO(self.source_png)) as source:
            dialog.image = ImageTk.PhotoImage(source, master=dialog.window)
        canvas = tk.Canvas(dialog.body, highlightthickness=0)
        vertical = ttk.Scrollbar(dialog.body, orient="vertical", command=canvas.yview)
        horizontal = ttk.Scrollbar(dialog.body, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=dialog.image)
        canvas.configure(scrollregion=(0, 0, dialog.image.width(), dialog.image.height()))
        dialog.status_var.set("Original image saved locally. Viewing it does not record quantities.")
        return dialog

    def catalog_names(self):
        return tuple(item["name"] for item in self.owner.controller.session()["catalog"].values())

    def read_text(self):
        try:
            text = self.text.get("1.0", "end-1c")
            if self.image_result is not None and text != self.image_result.raw_text:
                correction = messagebox.askyesnocancel("Text source",
                    "Is this text a correction of the loaded image?\n\n"
                    "Yes keeps the image as its source. No treats the text as a new pasted source.\n"
                    "Cancel returns to editing without reviewing it.", parent=self.window, default="cancel")
                if correction is None:
                    return
                if not correction:
                    self.new_pasted_source(keep_text=True)
            self.clear_review()
            if self.image_result is not None and text == self.image_result.raw_text:
                result = self.image_result
            else:
                result = parse_item_text(text, self.catalog_names(), mode=self.mode_var.get())
                if self.source_png is not None:
                    result = replace(result, source_png=self.source_png, ocr_texts=self.image_result.ocr_texts,
                        warnings=(*result.warnings, "Text was manually edited from an image. Verify corrections against the original image."))
            self.accept_result(result)
        except (ValueError, OSError) as error:
            self.error(error)

    def load_image(self):
        path = filedialog.askopenfilename(parent=self.window, title="Select a saved item screenshot",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if path:
            self.launch_image(lambda: _load_image(path))

    def select_area(self):
        if self.owner.select_region is None or self.busy:
            return
        self.hidden = True
        windows = [self.owner.root, self.owner.window, *(dialog.window for dialog in self.owner.dialogs)]
        self.hidden_states = [(window, window.state()) for window in dict.fromkeys(windows) if window.winfo_exists()]
        for window, state in self.hidden_states:
            window.withdraw()
        self.window.update_idletasks()
        try:
            selector = self.owner.select_region(self.owner.root, self.area_selected)
            if selector is not None and hasattr(selector, "canvas"):
                for item in selector.canvas.find_all():
                    if selector.canvas.type(item) == "text":
                        selector.canvas.itemconfigure(item, text="Drag around visible item names and quantities (Esc to cancel)")
        except Exception as error:
            self.restore()
            self.error(error)

    def area_selected(self, region):
        if self.closed:
            return
        if region is None:
            self.restore()
            self.status_var.set("Selection cancelled. Nothing was captured or recorded.")
            return
        # Let the selector leave the compositor before the explicit capture.
        capture = self.owner.capture
        self.capture_job = self.window.after(180, lambda: self.launch_image(lambda: capture(region), restore=True))

    def restore(self):
        if self.hidden:
            self.hidden = False
            states = getattr(self, "hidden_states", [(self.owner.window, "normal"), (self.window, "normal")])
            for window, state in states:
                if window.winfo_exists():
                    window.state(state)
            self.hidden_states = []

    def launch_image(self, source, *, restore=False):
        if self.closed or self.busy:
            return
        self.source_png = None
        self.image_result = None
        self.clear_review()
        self.busy = True
        for button in self.read_buttons:
            button.state(["disabled"])
        self.mode_box.state(["disabled"])
        self.status_var.set("Reading image locally. You can still move or close this window.")
        mode, items = self.mode_var.get(), self.catalog_names()
        reader, output = self.owner.image_reader, self.queue

        def work():
            try:
                image = source()
                if restore:
                    output.put(("restore", None))
                result = reader(image, items, mode=mode)
                output.put(("result", result))
            except Exception as error:
                output.put(("error", f"Image reading failed: {error}. Use pasted text or manual entry."))

        threading.Thread(target=work, name="loot-image-read", daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.restore()
                if kind == "restore":
                    continue
                self.busy = False
                self.mode_box.state(["!disabled"])
                for button in self.read_buttons:
                    button.state(["!disabled"])
                if self.owner.select_region is None:
                    self.read_buttons[2].state(["disabled"])
                if kind == "error":
                    self.status_var.set(payload)
                else:
                    try:
                        self.accept_result(payload)
                    except (ValueError, OSError) as error:
                        self.error(error)
        except queue.Empty:
            pass
        self.poll_job = self.window.after(50, self.poll)

    def accept_result(self, result):
        if self.session_id != self.owner.controller.session()["id"]:
            raise ValueError("The active outing changed. Close this reading and start a new one.")
        self.evidence = self.owner.controller.prepare_read(result)
        self.status_var.set("Evidence saved locally. Review required before recording.")
        if result.source_png is not None:
            if self.image_result is None or self.source_png != result.source_png:
                self.image_result = result
            self.source_png = result.source_png
            self.text.delete("1.0", "end")
            self.text.insert("1.0", result.raw_text)
            self.preview_button.state(["!disabled"])
        self.text.edit_modified(False)
        self.review_text = self.text.get("1.0", "end-1c")
        warning = "\n".join(result.warnings[:8])
        if len(result.warnings) > 8:
            warning += f"\n{len(result.warnings) - 8} more warnings are preserved with the evidence."
        if not result.items:
            warning += "\nNo usable quantities. Check OCR setup, paste text, or enter a manual activity/snapshot."
        if result.conflicted_names:
            warning += "\nUnknown due to conflicting readings: " + ", ".join(result.conflicted_names)
        self.set_warnings(warning or "Check each proposed quantity against the source.")
        catalog = self.owner.controller.session()["catalog"]
        for row, (item_id, count) in enumerate(self.owner.controller.proposed_counts(self.evidence).items()):
            ttk.Label(self.proposals.body, text=catalog[item_id]["name"]).grid(row=row, column=0, sticky="w", pady=4)
            variable = self.count_vars[item_id] = tk.StringVar(self.window, count)
            ttk.Entry(self.proposals.body, textvariable=variable, width=16).grid(row=row, column=1, padx=12)
        self.confirm_var.set(False)
        self.confirm_text.set("I reviewed the source and edited quantities. ? leaves an inventory count unknown."
                              if result.mode == "inventory" else "I reviewed the source and edited quantities. ? skips a pickup proposal.")
        self.confirm_button.configure(text="Review full inventory snapshot" if result.mode == "inventory" else "Confirm reviewed pickups")
        if result.items or result.mode == "inventory":
            self.confirm_button.state(["!disabled"])

    def submit(self):
        if self.evidence is None:
            return
        if self.text.get("1.0", "end-1c") != self.review_text:
            self.clear_review()
            self.status_var.set("Source text changed. Review the edited text before confirming quantities.")
            return
        try:
            self.owner.controller._confirmed(self.confirm_var.get())
            if self.evidence.result.mode == "inventory":
                dialog = self.owner.new_snapshot(self.evidence)
                for key, variable in self.count_vars.items():
                    dialog.count_vars[key].set(variable.get())
                self.close()
                return
            added = self.owner.controller.confirm_pickups(self.evidence,
                {key: variable.get() for key, variable in self.count_vars.items()}, confirmed=True)
        except OSError as error:
            self.error(f"{error}. Some entries may already be saved. Check the journal; retrying this evidence will not duplicate them.")
            return
        except ValueError as error:
            self.error(error)
            return
        self.owner.refresh()
        if added:
            self.status_var.set(f"Saved {added} pickup entries. Corrections use manual adjustments.")
        elif any(variable.get().strip() not in ("", "?") for variable in self.count_vars.values()):
            self.status_var.set("Already confirmed from this source. Use a manual adjustment to correct a miscount, or New pasted source only for a different drop.")
        else:
            self.status_var.set("No pickup entries selected. Nothing was recorded.")
        self.confirm_var.set(False)

    def close(self):
        self.restore()
        self.closed = True
        self.cancel_jobs()
        super().close()

    def cancel_jobs(self):
        if self.poll_job:
            self.window.after_cancel(self.poll_job)
            self.poll_job = None
        if self.capture_job:
            self.window.after_cancel(self.capture_job)
            self.capture_job = None

    def destroyed(self, event):
        if event.widget == self.window:
            self.closed = True
            self.cancel_jobs()
        super().destroyed(event)


class ProfitWindow:
    def __init__(self, root, path, *, wallet=None, select_region=None, image_reader=read_item_image, capture=_capture_region):
        self.controller = LootController(path)
        self.root, self.wallet, self.select_region = root, wallet, select_region
        self.image_reader, self.capture = image_reader, capture
        self.selected_session = None
        self.dialogs = []
        self.window = tk.Toplevel(root)
        self.window.title("Maple Loot Overlay | Loot and profit")
        self.window.geometry("1080x740")
        self.window.minsize(880, 600)
        body = ttk.Frame(self.window, padding=14)
        body.pack(fill="both", expand=True)
        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New outing", command=self.new_session).pack(side="left")
        self.active_buttons = []
        for text, command in (("Record activity", self.record_event), ("New snapshot", self.new_snapshot), ("Read items", self.read_items)):
            button = ttk.Button(toolbar, text=text, command=command)
            button.pack(side="left", padx=(7, 0))
            self.active_buttons.append(button)
        self.history = ttk.Combobox(toolbar, state="readonly", width=40)
        self.history.pack(side="right")
        self.history.bind("<<ComboboxSelected>>", self.choose_history)
        ttk.Label(body, text="Profit uses confirmed wallet and inventory snapshots with catalog prices. Journal entries are a separate activity breakdown.",
                  wraplength=1000).pack(anchor="w", pady=(10, 4))
        self.profit_var, self.coverage_var, self.cash_var = (tk.StringVar(self.window) for _ in range(3))
        ttk.Label(body, textvariable=self.profit_var, font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=4)
        ttk.Label(body, textvariable=self.coverage_var, wraplength=1000).pack(anchor="w")
        ttk.Label(body, textvariable=self.cash_var, wraplength=1000).pack(anchor="w", pady=7)
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill="both", expand=True)
        self.inventory_tab = ttk.Frame(self.tabs, padding=8)
        self.journal_tab = ttk.Frame(self.tabs, padding=8)
        self.catalog_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.inventory_tab, text="Snapshot and pickups")
        self.tabs.add(self.journal_tab, text="Journal and snapshot history")
        self.tabs.add(self.catalog_tab, text="Catalog for future outings")
        self.inventory = self.table(self.inventory_tab, ("Item", "Opening", "Latest", "Change", "Pickups (whole outing journal)", "Unit price", "Value change"))
        self.inventory.column("Pickups (whole outing journal)", width=210)
        self.journal = self.table(self.journal_tab, ("Time", "Activity", "Item", "Units", "Mesos", "Note"))
        ttk.Label(self.journal_tab, text="Double-click a row for full record details. Corrections append to the journal.").pack(anchor="w", pady=5)
        self.journal.bind("<Double-1>", self.show_record)
        self.catalog_table = self.table(self.catalog_tab, ("Item", "Kind", "Unit price"))
        self.catalog_table.bind("<<TreeviewSelect>>", self.select_catalog)
        editor = ttk.Frame(self.catalog_tab)
        editor.pack(fill="x", pady=8)
        self.catalog_id = None
        self.catalog_name = tk.StringVar(self.window)
        self.catalog_kind = tk.StringVar(self.window, "loot")
        self.catalog_price = tk.StringVar(self.window, "?")
        for column, (label, variable) in enumerate((("Name", self.catalog_name), ("Kind", self.catalog_kind), ("Mesos per item", self.catalog_price))):
            ttk.Label(editor, text=label).grid(row=0, column=column, sticky="w")
            field = ttk.Combobox(editor, textvariable=variable, values=("loot", "supply"), state="readonly", width=12) if column == 1 else ttk.Entry(editor, textvariable=variable, width=28 if column == 0 else 18)
            field.grid(row=1, column=column, padx=(0, 8), sticky="ew")
        ttk.Button(editor, text="Save item / price", command=self.save_catalog).grid(row=1, column=3, padx=5)
        ttk.Button(editor, text="New custom item", command=self.clear_catalog).grid(row=1, column=4)
        suggestions = ttk.Frame(self.catalog_tab)
        suggestions.pack(fill="x", pady=(0, 8))
        self.suggestion_button = ttk.Button(suggestions, text="Use MeowDB suggestion", command=self.use_price_suggestion)
        self.suggestion_button.pack(side="left")
        ttk.Label(suggestions, text="Bundled MeowDB prices. Review the source and check date, then Save item / price.",
                  wraplength=650).pack(side="left", padx=10)
        ttk.Label(self.catalog_tab, text="Loot uses observed NPC value; supplies use replacement cost. ? means unknown.\n"
                  "Star price is per individual star. Catalog edits apply to the next outing; existing prices remain frozen.", wraplength=950).pack(anchor="w")
        self.status_var = tk.StringVar(self.window)
        ttk.Label(body, textvariable=self.status_var, foreground="#9b2525", wraplength=1000).pack(anchor="w", pady=6)
        self.refresh()

    @staticmethod
    def table(parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        scroll.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)
        for name in columns:
            tree.heading(name, text=name)
            tree.column(name, width=170 if name in ("Item", "Time", "Note") else 95, minwidth=75)
        return tree

    def new_session(self):
        return SnapshotDialog(self, opening=True)

    def new_snapshot(self, evidence=None):
        return SnapshotDialog(self, evidence=evidence)

    def record_event(self):
        return EventDialog(self)

    def read_items(self):
        return ReadDialog(self)

    def choose_history(self, event=None):
        index = self.history.current()
        self.selected_session = self.history_ids[index] if index >= 0 else None
        self.refresh()

    def refresh(self):
        ledger = self.controller.ledger
        sessions = ledger.sessions
        active = ledger.active_session
        selected = next((row for row in sessions if row["id"] == self.selected_session), active)
        self.history_ids = [row["id"] for row in sessions]
        self.history.configure(values=[f"{row['name']} | {local_time(row['started_at'])}" for row in sessions])
        if selected:
            self.history.current(self.history_ids.index(selected["id"]))
        for button in self.active_buttons:
            button.state(["!disabled"] if selected and selected["id"] == active["id"] else ["disabled"])
        summary = ledger.summary(None if selected is None else selected["id"])
        self.profit_var.set("Profit at this outing's prices unknown" if summary["profit"] is None else f"Profit at this outing's prices {shown(summary['profit'])} mesos")
        reasons = "; ".join(summary["missing"])
        if summary["stale"]:
            reasons = "STALE: journal activity is newer than this snapshot. " + reasons
        if summary["as_of"] is not None and time.time() - summary["as_of"] > 300:
            reasons = "Older checkpoint, current holdings are not confirmed. " + reasons
        self.coverage_var.set(f"As of {local_time(summary['as_of'])}. " + (reasons or "All checkpoint counts and required prices are known."))
        self.cash_var.set(f"Snapshot cash change {shown(summary['wallet_delta'])}; inventory value change {shown(summary['inventory_delta_value'])}; "
            f"external transfers through snapshot {shown(summary['external_transfer_net'])}.\n"
            f"Journal: cash pickups {shown(summary['pickup_mesos'])}, sales {shown(summary['sales_mesos'])}, "
            f"purchases {shown(summary['purchase_mesos'])}, recharges {shown(summary['recharge_mesos'])}, "
            f"transfers {shown(summary['journal_transfer_net'])}, adjustments {shown(summary['adjustment_mesos'])}, "
            f"net recorded cash incl. transfers/adjustments {shown(summary['cashflow_mesos'])}. Hourly snapshot rate {shown(summary['profit_per_hour'])}.")
        for tree in (self.inventory, self.journal, self.catalog_table):
            tree.delete(*tree.get_children())
        self.records = {}
        if selected:
            for item_id, item in selected["catalog"].items():
                change = summary.get("inventory_changes", {}).get(item_id, {})
                self.inventory.insert("", "end", values=(item["name"], shown(selected["opening"]["counts"][item_id]),
                    shown(change.get("closing")), shown(change.get("quantity_delta")),
                    shown(summary["confirmed_pickups"].get(item_id, 0)), shown(item["unit_value"]), shown(change.get("value_delta"))))
            opening = {"type": "opening", **selected["opening"]}
            for index, record in enumerate([opening, *selected["records"]]):
                identity = str(index)
                self.records[identity] = record
                item = selected["catalog"].get(record.get("item_id"), {}).get("name", "")
                self.journal.insert("", "end", iid=identity, values=(local_time(record["timestamp"]), record.get("kind", record["type"]),
                    item, shown(record["quantity"]) if record["type"] == "event" else "",
                    shown(record["mesos"]) if record["type"] == "event" else "wallet " + shown(record["wallet"]),
                    record.get("note", "Wallet + counts; double-click for details")))
        for item_id, item in ledger.catalog.items():
            self.catalog_table.insert("", "end", iid=item_id, values=(item["name"], item["kind"], shown(item["unit_value"])))

    def show_record(self, event=None):
        selection = self.journal.selection()
        if not selection:
            return
        dialog = Dialog(self, "Saved record details")
        text = ScrolledText(dialog.body, wrap="word", width=80, height=24)
        text.pack(fill="both", expand=True)
        text.insert("1.0", json.dumps(self.records[selection[0]], ensure_ascii=False, indent=2, default=str))
        text.configure(state="disabled")

    def select_catalog(self, event=None):
        selected = self.catalog_table.selection()
        if selected:
            self.catalog_id = selected[0]
            item = self.controller.ledger.catalog[self.catalog_id]
            self.catalog_name.set(item["name"])
            self.catalog_kind.set(item["kind"])
            self.catalog_price.set("?" if item["unit_value"] is None else str(item["unit_value"]))

    def clear_catalog(self):
        self.catalog_id = None
        self.catalog_table.selection_remove(*self.catalog_table.selection())
        self.catalog_name.set("")
        self.catalog_kind.set("loot")
        self.catalog_price.set("?")

    def use_price_suggestion(self):
        suggestion = price_suggestion(self.catalog_name.get(), self.catalog_kind.get())
        if suggestion is None:
            self.status_var.set("No verified MeowDB suggestion for this exact item and kind. Price unchanged; enter an observed per-item value.")
            return
        self.catalog_price.set(suggestion.value)
        self.status_var.set(f"MeowDB suggestion: {suggestion.basis}, checked {suggestion.checked_on}. "
                            f"{suggestion.url} Review, then Save item / price to use it for future outings.")

    def save_catalog(self):
        try:
            self.controller.set_item(self.catalog_id or "custom_" + uuid.uuid4().hex, self.catalog_name.get(),
                                     self.catalog_kind.get(), self.catalog_price.get())
        except (ValueError, OSError) as error:
            self.status_var.set(str(error))
            return
        self.status_var.set("Catalog saved for future outings.")
        self.refresh()
        self.clear_catalog()
