"""Small, dated MeowDB price suggestions. No network calls or automatic writes.

Loot uses NPC sell-back. Supplies use shop purchase costs, except existing Subi
stacks, whose individual stars use the listed recharge cost. Source details and
limits are in docs/price-sources.md. Existing user prices are never replaced here.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceSuggestion:
    value: str
    basis: str
    url: str
    checked_on: str = "2026-10-16"


def _item(value, basis, page):
    return PriceSuggestion(value, basis, f"https://meowdb.com/msclassic/item-db/{page}")


_SUGGESTIONS = {
    ("red snail shell", "loot"): _item("4", "NPC sell-back per item", 351),
    ("blue snail shell", "loot"): _item("2", "NPC sell-back per item", 349),
    ("mushroom spore", "loot"): _item("3", "NPC sell-back per item", 350),
    ("subi throwing-stars", "supply"): _item("0.3", "Recharge per individual star", 294),
    ("subi throwing stars", "supply"): _item("0.3", "Recharge per individual star", 294),
    ("pet food", "supply"): _item("35", "NPC shop buy per item at Arturo", 269),
    ("red potion", "supply"): _item("50", "NPC shop buy per item", 270),
    ("orange potion", "supply"): _item("150", "NPC shop buy per item", 271),
    ("white potion", "supply"): _item("350", "NPC shop buy per item", 272),
    ("blue potion", "supply"): _item("220", "NPC shop buy per item", 273),
}


def price_suggestion(name, kind):
    """Return only an exact named/kinded reference, allowing case and edge spaces."""
    if not isinstance(name, str) or kind not in ("loot", "supply"):
        return None
    return _SUGGESTIONS.get((name.strip().casefold(), kind))
