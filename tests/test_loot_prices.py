"""Checked reference prices must keep sale, purchase and recharge units distinct."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loot_prices import price_suggestion


class PriceSuggestionTests(unittest.TestCase):
    def test_loot_uses_npc_sale_value_per_item(self):
        for name, value, page in (("Red Snail Shell", "4", "351"),
                                   ("Blue Snail Shell", "2", "349"),
                                   ("Mushroom Spore", "3", "350")):
            with self.subTest(name=name):
                result = price_suggestion(name, "loot")
                self.assertEqual(result.value, value)
                self.assertEqual(result.basis, "NPC sell-back per item")
                self.assertEqual(result.url, f"https://meowdb.com/msclassic/item-db/{page}")
                self.assertEqual(result.checked_on, "2026-10-16")

    def test_stars_use_recharge_cost_not_stack_purchase_or_sale_price(self):
        for name in ("Subi Throwing-Stars", "Subi Throwing Stars"):
            result = price_suggestion(name, "supply")
            self.assertEqual(Decimal(result.value) * 500, Decimal("150"))
            self.assertEqual(result.basis, "Recharge per individual star")
            self.assertTrue(result.url.endswith("/294"))
        self.assertIsNone(price_suggestion("Subi Throwing Stars", "loot"))

    def test_named_supplies_use_shop_buy_prices(self):
        for name, value in (("Pet Food", "35"), ("Red Potion", "50"),
                            ("Orange Potion", "150"), ("White Potion", "350"), ("Blue Potion", "220")):
            with self.subTest(name=name):
                result = price_suggestion(name, "supply")
                self.assertEqual(result.value, value)
                self.assertEqual(result.basis, "NPC shop buy per item at Arturo" if name == "Pet Food" else "NPC shop buy per item")

    def test_unknown_generic_and_wrong_kind_do_not_guess(self):
        for name, kind in (("HP Potion", "supply"), ("MP Potion", "supply"),
                           ("Red Potion", "loot"), ("Red Snail", "loot"),
                           ("Red Snail Shell", "supply"), (None, "loot"), ("", "loot")):
            self.assertIsNone(price_suggestion(name, kind))
        self.assertEqual(price_suggestion(" red snail shell ", "loot").value, "4")

    def test_suggestions_are_immutable(self):
        result = price_suggestion("Red Snail Shell", "loot")
        with self.assertRaises(FrozenInstanceError):
            result.value = "999"
        self.assertEqual(price_suggestion("Red Snail Shell", "loot").value, "4")


if __name__ == "__main__":
    unittest.main()
