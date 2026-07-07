import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import apply_logo_overlay, build_logo_prompt_suffix, normalize_logo_position


class TestLogoOverlay(unittest.TestCase):
    def test_normalize_logo_position(self):
        self.assertEqual(normalize_logo_position("top_right"), "top_right")
        self.assertEqual(normalize_logo_position("invalid"), "top_left")

    def test_build_logo_prompt_suffix(self):
        text = build_logo_prompt_suffix("top_right")
        self.assertIn("右上角", text)
        self.assertIn("替换", text)

    def test_apply_logo_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.png"
            logo = Path(tmp) / "logo.png"
            Image.new("RGB", (400, 300), color=(200, 200, 200)).save(base)
            Image.new("RGBA", (80, 40), color=(255, 0, 0, 200)).save(logo)
            apply_logo_overlay(base, logo, "top_left")
            with Image.open(base) as img:
                px = img.getpixel((10, 10))
                self.assertGreater(px[0], 150)


if __name__ == "__main__":
    unittest.main()
