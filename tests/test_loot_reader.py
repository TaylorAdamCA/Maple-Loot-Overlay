"""Assisted reader tests. Synthetic inputs do not establish live calibration."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loot_reader import parse_item_text
import loot_reader


CATALOG = {
    "Red Snail Shell": ("Red Snail Shells",),
    "Blue Snail Shell": ("Blue Snail Shells",),
    "Mushroom Spore": ("Mushroom Spores",),
    "Subi Throwing-Stars": (),
}


class ItemTextTests(unittest.TestCase):
    def test_exact_quantity_is_only_a_review_proposal(self):
        result = parse_item_text("Red Snail Shell x 12", ["Red Snail Shell"],
                                 mode="pickup")
        self.assertEqual([(item.name, item.quantity) for item in result.items],
                         [("Red Snail Shell", 12)])
        self.assertEqual(result.items[0].mode, "pickup")
        self.assertEqual(result.raw_text, "Red Snail Shell x 12")
        self.assertTrue(result.review_required)

    def test_full_phrases_use_exact_normalized_names_and_explicit_aliases(self):
        text = "  RED   SNAIL SHELLS x 1,234\n12 Blue Snail Shell\nObtained 3 Mushroom Spores"
        result = parse_item_text(text, CATALOG, mode="pickup")
        self.assertEqual([(item.name, item.quantity) for item in result.items],
                         [("Red Snail Shell", 1234), ("Blue Snail Shell", 12),
                          ("Mushroom Spore", 3)])
        self.assertEqual(result.items[0].source_lines, ("  RED   SNAIL SHELLS x 1,234",))
        self.assertEqual(result.raw_text, text)

    def test_related_unknown_items_are_never_fuzzy_matched(self):
        for text in ("Slime & Mushroom Omok x 2", "Mushroom x 2",
                     "Mushroom Spore Powder x 2", "Red Snail Shell fragment x 2",
                     "3 Mushroom Spores and 2 Red Snail Shell",
                     "Sold 3 Mushroom Spore", "Lost 3 Mushroom Spore",
                     "Not Obtained 3 Mushroom Spore"):
            with self.subTest(text=text):
                result = parse_item_text(text, CATALOG, mode="pickup")
                self.assertEqual(result.items, ())
                self.assertTrue(result.warnings)
                self.assertEqual(result.raw_text, text)

    def test_missing_bad_or_overlarge_quantities_are_unknown(self):
        for quantity in ("", "-1", "+1", "1.5", "1,00", "1,000,00", "1,000.0",
                         "1 000", "01", "9" * 40):
            with self.subTest(quantity=quantity):
                result = parse_item_text("Red Snail Shell x " + quantity,
                                         CATALOG, mode="inventory")
                self.assertEqual(result.items, ())
                self.assertTrue(result.warnings)
        self.assertEqual(parse_item_text("Red Snail Shell", CATALOG).items, ())

    def test_duplicate_lines_and_aliases_do_not_double_count(self):
        text = "Red Snail Shell x 12\nRed Snail Shell x 12\n12 Red Snail Shells"
        result = parse_item_text(text, CATALOG)
        self.assertEqual([(item.name, item.quantity) for item in result.items],
                         [("Red Snail Shell", 12)])
        self.assertTrue(any("duplicate" in warning.lower() for warning in result.warnings))
        self.assertEqual(len(result.items[0].source_lines), 2)

    def test_conflicting_repeated_quantities_exclude_the_item(self):
        result = parse_item_text("Red Snail Shell x 12\nRed Snail Shell x 13\n3 Mushroom Spore",
                                 CATALOG, mode="inventory")
        self.assertEqual([(item.name, item.quantity) for item in result.items],
                         [("Mushroom Spore", 3)])
        self.assertIn("Red Snail Shell", result.conflicted_names)
        self.assertTrue(any("conflict" in warning.lower() for warning in result.warnings))

    def test_malformed_quantity_for_a_known_item_blocks_its_other_reading(self):
        for invalid in ("Red Snail Shell x -1", "Red Snail Shell x 1,00",
                        "Red Snail Shell x 0", "Red Snail Shell x " + "9" * 40):
            with self.subTest(invalid=invalid):
                result = parse_item_text("Red Snail Shell x 12\n" + invalid, CATALOG)
                self.assertEqual(result.items, ())
                self.assertIn("Red Snail Shell", result.conflicted_names)

    def test_bare_known_name_or_alias_blocks_its_other_quantity(self):
        for bare_name in ("Red Snail Shell", "  RED   SNAIL SHELLS  "):
            for mode in ("pickup", "inventory"):
                with self.subTest(bare_name=bare_name, mode=mode):
                    result = parse_item_text("Red Snail Shell x 12\n" + bare_name,
                                             CATALOG, mode=mode)
                    self.assertEqual(result.items, ())
                    self.assertIn("Red Snail Shell", result.conflicted_names)
                    self.assertTrue(result.warnings)

    def test_quantity_ceiling_matches_ledger_supported_range(self):
        for text, quantity in (("999999999999999999", 999999999999999999),
                               ("1000000000000000000", 1000000000000000000),
                               ("1,000,000,000,000,000,000", 1000000000000000000)):
            with self.subTest(text=text):
                result = parse_item_text("Red Snail Shell x " + text, CATALOG)
                self.assertEqual([item.quantity for item in result.items], [quantity])
        for text in ("1000000000000000001", "1,000,000,000,000,000,001", "9223372036854775807"):
            with self.subTest(text=text):
                result = parse_item_text("Red Snail Shell x " + text, CATALOG)
                self.assertEqual(result.items, ())
                self.assertIn("Red Snail Shell", result.conflicted_names)

    def test_ambiguous_catalog_alias_never_selects_an_arbitrary_item(self):
        result = parse_item_text("Shell x 2", {"Red Snail Shell": ("Shell",),
                                               "Blue Snail Shell": ("Shell",)})
        self.assertEqual(result.items, ())
        self.assertTrue(result.warnings)

    def test_inventory_zero_is_explicit_and_never_inferred_from_missing_text(self):
        result = parse_item_text("Red Snail Shell x 0", CATALOG, mode="inventory")
        self.assertEqual([(item.name, item.quantity, item.mode) for item in result.items],
                         [("Red Snail Shell", 0, "inventory")])
        for mode in ("pickup", "inventory"):
            empty = parse_item_text(" \n\t", CATALOG, mode=mode)
            self.assertEqual(empty.items, ())
            self.assertTrue(empty.warnings)
        self.assertEqual(parse_item_text("Red Snail Shell x 0", CATALOG).items, ())

    def test_inventory_mode_rejects_acquisition_phrases(self):
        result = parse_item_text("Obtained 3 Mushroom Spore", CATALOG, mode="inventory")
        self.assertEqual(result.items, ())
        self.assertTrue(result.warnings)

    def test_unsupported_mode_is_not_silently_reinterpreted(self):
        with self.assertRaises(ValueError):
            parse_item_text("Red Snail Shell x 1", CATALOG, mode="sale")

    def test_oversize_text_is_unknown(self):
        result = parse_item_text("Red Snail Shell x 1\n" * 10000, CATALOG)
        self.assertEqual(result.items, ())
        self.assertTrue(result.warnings)


def synthetic_text_image():
    image = np.full((35, 320, 3), 255, dtype=np.uint8)
    cv2.putText(image, "Red Snail Shell x 12", (3, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 1)
    return image


class ItemImageTests(unittest.TestCase):
    def read_responses(self, responses, *, image=None, mode="pickup", items=CATALOG):
        response_iter = iter(responses)

        def ocr(image, *, config, timeout):
            self.assertEqual(image.dtype, np.uint8)
            self.assertEqual(image.ndim, 2)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 3)
            response = next(response_iter)
            if isinstance(response, Exception):
                raise response
            return response

        return loot_reader.read_item_image(
            synthetic_text_image() if image is None else image,
            items, mode=mode, ocr=ocr)

    def test_agreeing_variants_create_one_proposal_and_preserve_source(self):
        original = synthetic_text_image()
        before = original.copy()
        result = self.read_responses(["Red Snail Shell x 12", "12 Red Snail Shell", ""],
                                     image=original)
        self.assertEqual([(item.name, item.quantity) for item in result.items],
                         [("Red Snail Shell", 12)])
        self.assertTrue(result.review_required)
        self.assertEqual(result.ocr_texts, ("Red Snail Shell x 12", "12 Red Snail Shell", ""))
        self.assertIn("12 Red Snail Shell", result.raw_text)
        decoded = cv2.imdecode(np.frombuffer(result.source_png, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertTrue(np.array_equal(decoded, before))
        self.assertTrue(np.array_equal(original, before))

    def test_only_one_reading_cannot_establish_consensus(self):
        result = self.read_responses(["Red Snail Shell x 12", "", ""])
        self.assertEqual(result.items, ())
        self.assertTrue(result.warnings)

    def test_majority_does_not_overrule_a_conflicting_quantity(self):
        result = self.read_responses(["Red Snail Shell x 12", "Red Snail Shell x 12",
                                     "Red Snail Shell x 13"])
        self.assertEqual(result.items, ())
        self.assertIn("Red Snail Shell", result.conflicted_names)

    def test_within_variant_conflict_is_not_hidden_by_other_agreement(self):
        result = self.read_responses(["Red Snail Shell x 12", "Red Snail Shell x 12",
                                     "Red Snail Shell x 12\nRed Snail Shell x 13"])
        self.assertEqual(result.items, ())
        self.assertIn("Red Snail Shell", result.conflicted_names)

    def test_bare_known_name_in_one_variant_blocks_other_agreement(self):
        result = self.read_responses(["Red Snail Shell x 12", "Red Snail Shell x 12",
                                     "Red Snail Shell"])
        self.assertEqual(result.items, ())
        self.assertIn("Red Snail Shell", result.conflicted_names)

    def test_each_item_requires_its_own_consensus(self):
        result = self.read_responses(["Red Snail Shell x 12\nMushroom Spore x 3",
                                     "Red Snail Shell x 12", "Mushroom Spore x 4"])
        self.assertEqual([(item.name, item.quantity) for item in result.items],
                         [("Red Snail Shell", 12)])
        self.assertIn("Mushroom Spore", result.conflicted_names)

    def test_timeout_and_missing_binary_leave_quantities_unknown(self):
        for error in (RuntimeError("OCR timeout"), FileNotFoundError("No OCR binary")):
            with self.subTest(error=error):
                result = self.read_responses([error, "Red Snail Shell x 12", ""])
                self.assertEqual(result.items, ())
                self.assertTrue(result.warnings)
                self.assertTrue(result.source_png)

    def test_real_ocr_boundary_gets_bounded_timeout(self):
        variants = []

        def local_ocr(image, *, config, timeout):
            self.assertEqual(image.shape, (70, 640))
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 3)
            variants.append(image.copy())
            return "Red Snail Shell x 12"

        with patch("pytesseract.image_to_string", side_effect=local_ocr):
            result = loot_reader.read_item_image(synthetic_text_image(), CATALOG, mode="inventory")
        self.assertEqual([(item.quantity, item.mode) for item in result.items], [(12, "inventory")])
        self.assertEqual(len(variants), 3)
        self.assertTrue(any(not np.array_equal(variants[0], image) for image in variants[1:]))

    def test_invalid_blank_and_oversize_images_never_mean_zero(self):
        def forbidden_ocr(*args, **kwargs):
            self.fail("Invalid or blank images must not start OCR")

        for image in (None, np.zeros((0, 0, 3), dtype=np.uint8),
                      np.ones((10, 10, 3), dtype=np.float64),
                      np.zeros((100, 100, 3), dtype=np.uint8),
                      np.zeros((1, 5000, 3), dtype=np.uint8)):
            with self.subTest(shape=getattr(image, "shape", None)):
                result = loot_reader.read_item_image(image, CATALOG, mode="inventory", ocr=forbidden_ocr)
                self.assertEqual(result.items, ())
                self.assertTrue(result.warnings)

    def test_catalog_iterators_work_for_all_variants(self):
        result = self.read_responses(["Red Snail Shell x 12"] * 3,
                                     items=iter(["Red Snail Shell"]))
        self.assertEqual([item.quantity for item in result.items], [12])

    def test_large_virtual_images_are_rejected_before_any_processing(self):
        tiny = np.zeros((1, 1, 3), dtype=np.uint8)
        for shape in ((100_000, 100_000, 3), (2100, 2100, 3)):
            with self.subTest(shape=shape):
                huge = np.broadcast_to(tiny, shape)
                with patch("loot_reader.cv2.imencode", side_effect=AssertionError("processing oversize image")):
                    result = loot_reader.read_item_image(huge, CATALOG)
                self.assertEqual(result.items, ())
                self.assertIsNone(result.source_png)
                self.assertTrue(result.warnings)

    def test_alias_conflict_in_one_variant_blocks_other_exact_readings(self):
        catalog = {"Red Snail Shell": ("Shell",), "Blue Snail Shell": ("Shell",)}
        result = self.read_responses(["Red Snail Shell x 12", "Red Snail Shell x 12", "Shell x 12"],
                                     items=catalog)
        self.assertEqual(result.items, ())
        self.assertIn("Red Snail Shell", result.conflicted_names)


if __name__ == "__main__":
    unittest.main()
