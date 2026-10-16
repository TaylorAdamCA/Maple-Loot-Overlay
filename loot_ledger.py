"""Local, manually confirmed loot journal and inventory-based profit accounting.

Quantities are individual item units, including stars. Catalog prices are frozen
when each session starts. Journal transactions describe cash activity; only a
wallet/inventory snapshot establishes profit. No game or network access occurs.
"""
import copy
import json
import math
import os
import tempfile
import time
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


SCHEMA_VERSION = 1
MAX_INTEGER = 10 ** 18
DEFAULT_ITEMS = (
    ("red_shell", "Red Snail Shell", "loot"),
    ("blue_shell", "Blue Snail Shell", "loot"),
    ("mushroom_spore", "Mushroom Spore", "loot"),
    ("subi_stars", "Subi Throwing-Stars", "supply"),
    ("hp_potion", "HP Potion", "supply"),
    ("mp_potion", "MP Potion", "supply"),
    ("pet_food", "Pet Food", "supply"),
)


def _text(value, label, maximum=160, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f"{label} must be {'text' if empty else 'nonempty text'} of at most {maximum} characters")
    return value


def _integer(value, label, signed=False, unknown=False):
    if value is None and unknown:
        return None
    if type(value) is not int or abs(value) > MAX_INTEGER or (value < 0 and not signed):
        raise ValueError(f"{label} must be {'a signed' if signed else 'a nonnegative'} integer")
    return value


def _price(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Unit value must be a decimal string, Decimal, integer, or unknown")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid unit value") from None
    if not result.is_finite() or result < 0 or result > MAX_INTEGER or result.as_tuple().exponent < -18:
        raise ValueError("Unit value must be finite, nonnegative, at most 10^18, and have at most 18 decimal places")
    return format(result, "f")


def _timestamp(value=None):
    if value is None:
        value = time.time()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp datetime must include a timezone")
        value = value.timestamp()
    if type(value) not in (int, float):
        raise ValueError("Timestamp must be a finite Unix timestamp or aware datetime")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("Timestamp is out of range") from None
    if not math.isfinite(result) or not 0 <= result <= 253402300799:
        raise ValueError("Timestamp is out of range")
    return result


def _write_timestamp(value, latest=0):
    """Bound explicit new timestamps; keep default writes ordered after clock changes."""
    now = _timestamp()
    if value is None:
        return max(now, latest)
    timestamp = _timestamp(value)
    if timestamp > now + 60:
        raise ValueError("New timestamps cannot be more than 60 seconds in the future")
    return timestamp


def _counts(values, catalog):
    if not isinstance(values, dict) or any(key not in catalog for key in values):
        raise ValueError("Counts must use item IDs in the session catalog")
    return {key: _integer(values.get(key), f"Count for {key}", unknown=True) for key in catalog}


def _public(value):
    """Return a detached copy, decoding exact decimal prices for callers."""
    if isinstance(value, dict):
        result = {key: _public(item) for key, item in value.items()}
        if set(value) == {"name", "kind", "unit_value"} and value["kind"] in ("loot", "supply"):
            result["unit_value"] = None if value["unit_value"] is None else Decimal(value["unit_value"])
        return result
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _keys(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"Invalid {label} fields")


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON field: {key}")
        value[key] = item
    return value


def _json_constant(value):
    raise ValueError(f"Non-finite JSON value: {value}")


def _stored_timestamp(value):
    if type(value) not in (int, float):
        raise ValueError("Saved timestamp must be numeric")
    return _timestamp(value)


def _validate_catalog(catalog):
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("Catalog must contain items")
    for item_id, item in catalog.items():
        _text(item_id, "Item ID")
        _keys(item, ("name", "kind", "unit_value"), "catalog item")
        _text(item["name"], "Item name")
        if item["kind"] not in ("loot", "supply"):
            raise ValueError("Invalid item kind")
        if item["unit_value"] is not None and not isinstance(item["unit_value"], str):
            raise ValueError("Saved unit values must be decimal strings or null")
        _price(item["unit_value"])


def _validate_saved_counts(counts, catalog):
    _keys(counts, catalog, "inventory counts")
    _counts(counts, catalog)


class LootLedger:
    """Single-writer ledger. Every successful mutation is durably saved first.

    Invalid inputs raise ValueError; storage failures raise OSError without
    changing the previous in-memory state or replacing the previous file.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.exists():
            try:
                self._state = json.loads(self.path.read_text(encoding="utf-8"),
                                         object_pairs_hook=_json_object, parse_constant=_json_constant)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Cannot read loot ledger: {exc}") from exc
            self._validate_state(self._state)
        else:
            self._state = {
                "version": SCHEMA_VERSION,
                "catalog": {key: {"name": name, "kind": kind, "unit_value": None}
                            for key, name, kind in DEFAULT_ITEMS},
                "sessions": [],
                "active_session_id": None,
            }

    def _validate_state(self, state):
        _keys(state, ("version", "catalog", "sessions", "active_session_id"), "ledger")
        if type(state["version"]) is not int or state["version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported loot ledger schema")
        _validate_catalog(state["catalog"])
        if not isinstance(state["sessions"], list):
            raise ValueError("Sessions must be a list")
        session_ids = set()
        previous_timestamp = 0
        for session in state["sessions"]:
            _keys(session, ("id", "name", "started_at", "catalog", "opening", "records"), "session")
            _text(session["id"], "Session ID")
            _text(session["name"], "Session name")
            if session["id"] in session_ids:
                raise ValueError("Duplicate session ID")
            session_ids.add(session["id"])
            started = _stored_timestamp(session["started_at"])
            if started < previous_timestamp:
                raise ValueError("Sessions are out of timestamp order")
            _validate_catalog(session["catalog"])
            opening = session["opening"]
            _keys(opening, ("wallet", "counts", "timestamp"), "opening snapshot")
            _integer(opening["wallet"], "Opening wallet")
            _validate_saved_counts(opening["counts"], session["catalog"])
            if _stored_timestamp(opening["timestamp"]) != started:
                raise ValueError("Opening timestamp does not match session start")
            if not isinstance(session["records"], list):
                raise ValueError("Records must be a list")
            record_ids = set()
            previous_timestamp = started
            for record in session["records"]:
                if not isinstance(record, dict):
                    raise ValueError("Record must be an object")
                if record.get("type") == "event":
                    _keys(record, ("type", "id", "kind", "item_id", "quantity", "mesos", "note", "timestamp"), "event")
                    self._event(record["id"], record["kind"], record["item_id"], record["quantity"],
                                record["mesos"], record["note"], session["catalog"])
                elif record.get("type") == "snapshot":
                    _keys(record, ("type", "id", "wallet", "counts", "timestamp"), "snapshot")
                    _text(record["id"], "Snapshot ID")
                    _integer(record["wallet"], "Closing wallet", unknown=True)
                    _validate_saved_counts(record["counts"], session["catalog"])
                else:
                    raise ValueError("Unknown record type")
                if record["id"] in record_ids:
                    raise ValueError("Duplicate record ID")
                record_ids.add(record["id"])
                timestamp = _stored_timestamp(record["timestamp"])
                if timestamp < previous_timestamp:
                    raise ValueError("Records are out of timestamp order")
                previous_timestamp = timestamp
        expected_active = state["sessions"][-1]["id"] if state["sessions"] else None
        if state["active_session_id"] != expected_active:
            raise ValueError("Active session must be the latest saved session")

    @property
    def catalog(self):
        return _public(self._state["catalog"])

    @property
    def sessions(self):
        return _public(self._state["sessions"])

    @property
    def active_session(self):
        return _public(self._session())

    def _session(self, session_id=None, state=None):
        state = self._state if state is None else state
        session_id = state["active_session_id"] if session_id is None else session_id
        for session in state["sessions"]:
            if session["id"] == session_id:
                return session
        if session_id is not None:
            raise ValueError("Unknown session ID")
        return None

    def _commit(self, candidate):
        payload = json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self._state = candidate

    def save(self):
        self._commit(copy.deepcopy(self._state))

    def set_item(self, item_id, name, kind, unit_value):
        """Set a catalog price for future sessions; current session prices stay frozen."""
        _text(item_id, "Item ID")
        _text(name, "Item name")
        if kind not in ("loot", "supply"):
            raise ValueError("Item kind must be loot or supply")
        item = {"name": name, "kind": kind, "unit_value": _price(unit_value)}
        candidate = copy.deepcopy(self._state)
        candidate["catalog"][item_id] = item
        self._commit(candidate)

    def start_session(self, name, opening_wallet, opening_counts, timestamp=None):
        """Start a new outing without deleting previous sessions. Return its ID."""
        _text(name, "Session name")
        wallet = _integer(opening_wallet, "Opening wallet")
        counts = _counts(opening_counts, self._state["catalog"])
        active = self._session()
        latest = 0
        if active is not None:
            latest = active["records"][-1]["timestamp"] if active["records"] else active["started_at"]
        timestamp = _write_timestamp(timestamp, latest)
        if timestamp < latest:
            raise ValueError("New session cannot precede the previous session's latest record")
        candidate = copy.deepcopy(self._state)
        session_id = str(uuid.uuid4())
        session = {"id": session_id, "name": name, "started_at": timestamp,
                   "catalog": copy.deepcopy(candidate["catalog"]),
                   "opening": {"wallet": wallet, "counts": counts, "timestamp": timestamp},
                   "records": []}
        candidate["sessions"].append(session)
        candidate["active_session_id"] = session_id
        self._commit(candidate)
        return session_id

    def _append(self, record, timestamp):
        session = self._session()
        if session is None:
            raise ValueError("Start a session before recording activity")
        existing = next((row for row in session["records"] if row["id"] == record["id"]), None)
        if existing is not None:
            record["timestamp"] = existing["timestamp"] if timestamp is None else _timestamp(timestamp)
            if existing != record:
                raise ValueError("Record ID already exists with different data; use a new correction ID")
            return False
        latest = session["records"][-1]["timestamp"] if session["records"] else session["started_at"]
        record["timestamp"] = _write_timestamp(timestamp, latest)
        if record["timestamp"] < latest:
            raise ValueError("Records must be in timestamp order")
        candidate = copy.deepcopy(self._state)
        self._session(state=candidate)["records"].append(record)
        self._commit(candidate)
        return True

    def _event(self, event_id, kind, item_id, quantity, mesos, note, catalog):
        _text(event_id, "Event ID")
        _text(note, "Note", maximum=4000, empty=True)
        if kind not in ("pickup", "sale", "purchase", "recharge", "transfer", "adjustment"):
            raise ValueError("Unknown event kind")
        quantity = _integer(quantity, "Quantity", signed=kind == "adjustment")
        mesos = _integer(mesos, "Mesos", signed=kind in ("transfer", "adjustment"))
        if item_id is not None:
            _text(item_id, "Item ID")
            if item_id not in catalog:
                raise ValueError("Item is not in the frozen session catalog; start a new session to include it")
        if item_id is None and quantity != 0:
            raise ValueError("An item quantity requires an item ID")
        if kind == "transfer" and (item_id is not None or quantity != 0):
            raise ValueError("External transfers use signed mesos and no item quantity")
        if kind in ("sale", "purchase", "recharge") and item_id is None:
            raise ValueError("Sale, purchase, and recharge events require an item ID")
        return {"type": "event", "id": event_id, "kind": kind, "item_id": item_id,
                "quantity": quantity, "mesos": mesos, "note": note}

    def record_event(self, event_id, kind, item_id, quantity, mesos, note="", timestamp=None):
        """Append confirmed evidence. Same ID/data is a no-op; a conflict is an error.

        Sale, purchase and recharge mesos are nonnegative actual transaction
        totals. Transfers are signed external cash inflows/outflows; correct a
        mistaken transfer with a new signed counter-transfer. Adjustments may
        carry signed quantity and cash but affect journal totals only, never
        snapshot inventory or profit. Correct those with a new snapshot.

        Explicit new timestamps may be at most 60 seconds ahead of the clock.
        Default timestamps never precede the latest record if the clock moves
        backward. Identical retries retain their original timestamps.
        """
        session = self._session()
        if session is None:
            raise ValueError("Start a session before recording activity")
        record = self._event(event_id, kind, item_id, quantity, mesos, note, session["catalog"])
        return self._append(record, timestamp)

    def snapshot(self, snapshot_id, wallet, counts, timestamp=None):
        """Append a wallet/inventory checkpoint. Omitted counts remain unknown."""
        session = self._session()
        if session is None:
            raise ValueError("Start a session before recording a snapshot")
        record = {"type": "snapshot", "id": _text(snapshot_id, "Snapshot ID"),
                  "wallet": _integer(wallet, "Closing wallet", unknown=True),
                  "counts": _counts(counts, session["catalog"])}
        return self._append(record, timestamp)

    def summary(self, session_id=None):
        """Return journal totals plus the latest snapshot's profit and coverage.

        Profit is wallet change + valued inventory change - external transfer
        net through that snapshot. A later event makes this result stale, while
        preserving its original as-of time. Hourly rate uses snapshot duration.
        """
        session = self._session(session_id)
        result = {"session_id": None if session is None else session["id"],
                  "profit": None, "profit_per_hour": None, "elapsed_seconds": None,
                  "as_of": None, "stale": False, "missing": ["No closing snapshot"],
                  "snapshot_id": None, "wallet_delta": None, "inventory_delta_value": None,
                  "inventory_changes": {}, "external_transfer_net": 0, "journal_as_of": None,
                  "confirmed_pickups": {}, "confirmed_pickup_value": Decimal(0),
                  "adjustment_quantities": {}, "pickup_mesos": 0, "sales_mesos": 0,
                  "purchase_mesos": 0, "recharge_mesos": 0, "adjustment_mesos": 0,
                  "journal_transfer_net": 0, "cashflow_mesos": 0}
        if session is None:
            return result
        records = session["records"]
        snapshot_index = None
        with localcontext() as context:
            context.prec = 80
            for index, record in enumerate(records):
                result["journal_as_of"] = record["timestamp"]
                if record["type"] == "snapshot":
                    snapshot_index = index
                    continue
                kind, amount, item_id = record["kind"], record["mesos"], record["item_id"]
                total_key = {"pickup": "pickup_mesos", "sale": "sales_mesos", "purchase": "purchase_mesos",
                             "recharge": "recharge_mesos", "transfer": "journal_transfer_net",
                             "adjustment": "adjustment_mesos"}[kind]
                result[total_key] += amount
                result["cashflow_mesos"] += -amount if kind in ("purchase", "recharge") else amount
                if item_id is not None and kind in ("pickup", "adjustment"):
                    quantities = result["confirmed_pickups" if kind == "pickup" else "adjustment_quantities"]
                    quantities[item_id] = quantities.get(item_id, 0) + record["quantity"]
            for item_id, quantity in result["confirmed_pickups"].items():
                value = session["catalog"][item_id]["unit_value"]
                if quantity and value is None:
                    result["confirmed_pickup_value"] = None
                    break
                result["confirmed_pickup_value"] += Decimal(value or "0") * quantity
            if snapshot_index is None:
                return result
            closing = records[snapshot_index]
            result.update(snapshot_id=closing["id"], as_of=closing["timestamp"],
                          stale=any(row["type"] == "event" for row in records[snapshot_index + 1:]),
                          elapsed_seconds=closing["timestamp"] - session["started_at"], missing=[])
            result["external_transfer_net"] = sum(row["mesos"] for row in records[:snapshot_index + 1]
                                                   if row["type"] == "event" and row["kind"] == "transfer")
            if closing["wallet"] is None:
                result["missing"].append("Closing wallet is unknown")
            else:
                result["wallet_delta"] = closing["wallet"] - session["opening"]["wallet"]
            inventory_value = Decimal(0)
            inventory_complete = True
            for item_id, item in session["catalog"].items():
                opening_count, closing_count = session["opening"]["counts"][item_id], closing["counts"][item_id]
                delta = None if opening_count is None or closing_count is None else closing_count - opening_count
                value = None if item["unit_value"] is None else Decimal(item["unit_value"])
                valued_delta = Decimal(0) if delta == 0 else None if delta is None or value is None else delta * value
                if opening_count is None:
                    result["missing"].append(f"Opening count is unknown: {item_id}")
                if closing_count is None:
                    result["missing"].append(f"Closing count is unknown: {item_id}")
                if delta is not None and delta != 0 and value is None:
                    result["missing"].append(f"Unit price is unknown: {item_id}")
                if valued_delta is None:
                    inventory_complete = False
                else:
                    inventory_value += valued_delta
                result["inventory_changes"][item_id] = {"name": item["name"], "kind": item["kind"],
                    "opening": opening_count, "closing": closing_count, "quantity_delta": delta,
                    "unit_value": value, "value_delta": valued_delta}
            result["inventory_delta_value"] = inventory_value if inventory_complete else None
            if not result["missing"]:
                result["profit"] = Decimal(result["wallet_delta"]) + inventory_value - result["external_transfer_net"]
                if result["elapsed_seconds"] > 0:
                    result["profit_per_hour"] = result["profit"] * 3600 / Decimal(str(result["elapsed_seconds"]))
        return result
