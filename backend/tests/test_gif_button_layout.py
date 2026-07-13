import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import _parse_button_layout, parse_multipart
from gif_maker import make_breathing_gif


class TestGifButtonLayout(unittest.TestCase):
    def test_parse_button_layout_json(self):
        fields = {
            "button_layout": json.dumps({"x": 10, "y": 20, "w": 300, "h": 80}),
        }
        self.assertEqual(_parse_button_layout(fields), (10, 20, 300, 80))

    def test_parse_button_layout_fields(self):
        fields = {
            "button_x": "5",
            "button_y": "6",
            "button_width": "120",
            "button_height": "48",
        }
        self.assertEqual(_parse_button_layout(fields), (5, 6, 120, 48))

    def test_stretched_button_used_in_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            out = tmp / "out.gif"
            Image.new("RGBA", (400, 800), (255, 255, 255, 255)).save(bg)
            Image.new("RGBA", (80, 40), (255, 0, 0, 255)).save(btn)
            meta = make_breathing_gif(
                bg, btn, out,
                button_x=50, button_y=600, button_width=240, button_height=60,
            )
            self.assertEqual(meta["buttonWidth"], 240)
            self.assertEqual(meta["buttonHeight"], 60)


if __name__ == "__main__":
    unittest.main()
