"""Tests for finding things on screen: the real party list and inventory (cut from real
screenshots at three UI sizes) pasted over many kinds of background, solid and see-through.

Not finding something on a hard background is allowed (the tracker just waits for a
clearer look). A wrong answer, like a wrong mesos count or party size, is not.

Run from the project folder:  py -3 tests/test_screen.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_LOOT_DATA"] = tempfile.mkdtemp()          # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import maple_exp as m  # noqa: E402

FRAMES = Path(__file__).parent / "frames"
PARTS = json.loads((FRAMES / "screen_parts.json").read_text())
rnd = random.Random(1)
failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def backgrounds(h, w):
    """Plain, busy and tricky backgrounds (white like the box, gold like the coin, ...)."""
    g = np.random.default_rng(rnd.randint(0, 10 ** 6))
    yy, xx = np.mgrid[0:h, 0:w]
    blobs = cv2.GaussianBlur(g.integers(0, 256, (h, w, 3), dtype=np.uint8), (0, 0), 6)
    return {"black": np.zeros((h, w, 3), np.uint8), "white": np.full((h, w, 3), 255, np.uint8),
            "dark gray": np.full((h, w, 3), 45, np.uint8), "light gray": np.full((h, w, 3), 215, np.uint8),
            "gold": np.full((h, w, 3), (40, 190, 230), np.uint8),
            "noise": g.integers(0, 256, (h, w, 3), dtype=np.uint8),
            "dark noise": g.integers(0, 70, (h, w, 3), dtype=np.uint8),
            "blobs": cv2.normalize(blobs, None, 0, 255, cv2.NORM_MINMAX),
            "stripes": ((xx // 7 % 2) * 255).astype(np.uint8)[..., None].repeat(3, 2),
            "gradient": (xx * 255 // max(w, 1)).astype(np.uint8)[..., None].repeat(3, 2)}


# ---- mesos: found and read right (or not at all), solid and see-through
reader = m.ScreenReader(m.DigitTemplates.load())
templates = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz").templates()
for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    counts = {"solid read": [0, 0], "see-through read": [0, 0], "found see-through": [0, 0]}
    wrong = []
    for alpha in (1.0, 0.75, 0.6, 0.5, 0.4):
        for bg_name, bg in backgrounds(int(h * 1.6), int(w * 1.8)).items():
            oy, ox = rnd.randint(0, bg.shape[0] - h), rnd.randint(0, bg.shape[1] - w)
            img = bg.astype(np.float32)
            img[oy:oy + h, ox:ox + w] = alpha * inv + (1 - alpha) * img[oy:oy + h, ox:ox + w]
            img = img.astype(np.uint8)
            box = (ox + bx, oy + by, bw, bh)
            kind = "solid read" if alpha == 1 else "see-through read"
            got = m.read_mesos_positions(img, box, templates)
            counts[kind][0] += got is not None and got[0] == info["mesos"]
            counts[kind][1] += 1
            if got and got[0] != info["mesos"]:
                wrong.append(f"read {got[0]} ({bg_name}, {alpha:.0%} visible)")
            found = reader._find_seethrough_box(img, gh)
            if found:
                read = m.read_mesos_positions(img, found, templates)
                if not read or read[0] != info["mesos"]:
                    wrong.append(f"found a box that reads {read and read[0]} ({bg_name}, {alpha:.0%} visible)")
                counts["found see-through"][0] += 1
            counts["found see-through"][1] += 1
            solid = m.find_mesos_field(img, gh)
            if solid and reader._readable(img, solid):
                read = m.read_mesos_positions(img, solid, templates)
                if read and read[0] != info["mesos"]:
                    wrong.append(f"solid finder: read {read[0]} ({bg_name}, {alpha:.0%} visible)")
    size = f"EXP digits {gh:.0f} px"
    check(f"mesos never read wrong, UI size {size} ({', '.join(wrong) or 'no wrong reads'})", not wrong)
    ok, total = counts["solid read"]
    check(f"mesos read on a solid inventory, {size}: {ok}/{total}", ok == total)
    ok, total = counts["see-through read"]
    check(f"mesos read on a see-through inventory, {size}: {ok}/{total}", ok >= 0.85 * total)
    ok, total = counts["found see-through"]
    print(f"     (mesos box found by its outline, {size}: {ok}/{total})")

# ---- party: the right number of HP bars, from 1 to 6 members, on every background
for key, info in sorted(PARTS["party"].items(), key=lambda kv: kv[1]["gh"]):
    gh, pitch = info["gh"], info["pitch"]
    rows = [cv2.imread(str(FRAMES / r), cv2.IMREAD_UNCHANGED) for r in info["rows"]]
    rh, rw = rows[0].shape[:2]
    wrong, total = [], 0
    for members in range(1, 7):
        h = pitch * (members - 1) + rh
        for bg_name, bg in backgrounds(h + int(6 * gh), rw + int(8 * gh)).items():
            img = bg.copy()
            ox, oy = rnd.randint(0, img.shape[1] - rw), rnd.randint(0, img.shape[0] - h)
            for i in range(members):
                row = rows[i % len(rows)]
                region = img[oy + i * pitch:oy + i * pitch + rh, ox:ox + rw]
                a = row[..., 3:] / 255.0
                region[:] = (row[..., :3] * a + region * (1 - a)).astype(np.uint8)
            seen = max(1, len(m.find_party_bars(img, gh)))
            total += 1
            if seen != members:
                wrong.append(f"{members} as {seen} on {bg_name}")
    check(f"party size right, EXP digits {gh:.0f} px: {total - len(wrong)}/{total} "
          f"({', '.join(wrong[:5]) or 'all right'})", not wrong)
    clean = all(len(m.find_party_bars(bg, gh)) <= 1 for bg in backgrounds(900, 1400).values())
    check(f"no party list on screen means solo, EXP digits {gh:.0f} px", clean)

# ---- the class, from the status bar (the character name line is blanked in these)
m.pytesseract.pytesseract.tesseract_cmd = m.find_tesseract()
m.tesseract_ok = bool(m.pytesseract.pytesseract.tesseract_cmd)
if m.tesseract_ok:
    for name, info in sorted(PARTS["status"].items()):
        got = m.read_job(cv2.imread(str(FRAMES / name)), info["x0"], info["cy"], info["gh"])
        check(f"reads the class at EXP digits {info['gh']:.0f} px: {got}", got == info["job"])
else:
    print("     (Tesseract isn't installed: class reading not tested)")

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
