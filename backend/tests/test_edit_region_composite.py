import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import (
    compute_context_crop,
    composite_region_paste,
    composite_replacement_region,
    resize_image_cover,
)


class TestEditRegionHelpers(unittest.TestCase):
    def test_compute_context_crop_expands_within_bounds(self):
        cx, cy, cw, ch, ix, iy, iw, ih = compute_context_crop(100, 50, 200, 40, 800, 600)
        self.assertLessEqual(cx, 100)
        self.assertLessEqual(cy, 50)
        self.assertGreaterEqual(cx + cw, 300)
        self.assertGreaterEqual(cy + ch, 90)
        self.assertEqual((ix, iy, iw, ih), (100 - cx, 50 - cy, 200, 40))

    def test_resize_image_cover_preserves_aspect_without_stretch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "src.png"
            Image.new("RGB", (400, 100), color=(255, 0, 0)).save(path)
            resize_image_cover(path, 200, 50)
            with Image.open(path) as img:
                self.assertEqual(img.size, (200, 50))

    def test_composite_replacement_region_fits_without_stretch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.png"
            repl = Path(tmp) / "repl.png"
            out = Path(tmp) / "out.png"
            Image.new("RGB", (200, 200), color=(0, 0, 255)).save(base)
            Image.new("RGB", (120, 40), color=(255, 0, 0)).save(repl)
            composite_replacement_region(base, repl, out, 50, 60, 80, 40)
            with Image.open(out) as img:
                self.assertEqual(img.size, (200, 200))
                self.assertEqual(img.getpixel((0, 0)), (0, 0, 255))
                center = img.getpixel((90, 78))
                self.assertGreater(center[0], 200)
                self.assertLess(center[2], 50)

    def test_composite_region_paste_replaces_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.png"
            overlay = Path(tmp) / "overlay.png"
            out = Path(tmp) / "out.png"
            Image.new("RGB", (100, 100), color=(0, 0, 255)).save(base)
            Image.new("RGB", (20, 10), color=(255, 255, 0)).save(overlay)
            composite_region_paste(base, overlay, out, 10, 20, 20, 10)
            with Image.open(out) as img:
                self.assertEqual(img.getpixel((15, 25)), (255, 255, 0))
                self.assertEqual(img.getpixel((0, 0)), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()
