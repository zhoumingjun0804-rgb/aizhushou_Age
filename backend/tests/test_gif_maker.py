import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gif_maker import (
    compute_combined_transform,
    make_animated_gif,
    make_breathing_gif,
    merge_gif_layers_by_image,
)


class TestGifMaker(unittest.TestCase):
    def test_button_scale_changes_output_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            out_small = tmp / "small.gif"
            out_large = tmp / "large.gif"
            Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(bg)
            Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(btn)

            meta_small = make_breathing_gif(
                bg, btn, out_small,
                button_x=40, button_y=80, button_width=20, button_height=10,
            )
            meta_large = make_breathing_gif(
                bg, btn, out_large,
                button_x=40, button_y=80, button_width=60, button_height=30,
            )

            self.assertEqual(meta_small["buttonWidth"], 20)
            self.assertEqual(meta_large["buttonWidth"], 60)
            self.assertTrue(out_small.stat().st_size > 0)
            self.assertTrue(out_large.stat().st_size > 0)

    def test_combined_effects_merge_same_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            out = tmp / "combo.gif"
            Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(bg)
            Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(btn)

            merged = merge_gif_layers_by_image([
                (btn, ["breathing"], None),
                (btn, ["float"], None),
                (btn, ["sway"], None),
            ])
            self.assertEqual(len(merged), 1)
            self.assertEqual(set(merged[0][1]), {"breathing", "float", "sway"})

            merged_diff_layout = merge_gif_layers_by_image([
                (btn, ["breathing"], {"x": 10, "y": 20, "w": 30, "h": 30}),
                (btn, ["float"], {"x": 100, "y": 120, "w": 30, "h": 30}),
            ])
            self.assertEqual(len(merged_diff_layout), 2)

            meta = make_animated_gif(
                bg,
                [(btn, ["breathing"], None), (btn, ["float"], None)],
                out,
                button_x=40, button_y=80, button_width=40, button_height=20,
            )
            self.assertIn("breathing", meta["effects"])
            self.assertIn("float", meta["effects"])
            self.assertTrue(out.is_file())

    def test_float_only_effect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            out = tmp / "float.gif"
            Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(bg)
            Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(btn)

            meta = make_animated_gif(
                bg,
                [(btn, ["float"], None)],
                out,
                button_x=40, button_y=80, button_width=40, button_height=20,
            )
            self.assertEqual(meta["effects"], ["float"])
            t0 = compute_combined_transform(0.0, ["float"], "medium")
            t_quarter = compute_combined_transform(0.25, ["float"], "medium")
            self.assertEqual(t0["dx"], 0)
            self.assertNotEqual(t_quarter["dy"], 0)

    def test_per_layer_layouts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn_a = tmp / "a.png"
            btn_b = tmp / "b.png"
            out = tmp / "multi.gif"
            Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(bg)
            Image.new("RGBA", (30, 30), (255, 0, 0, 255)).save(btn_a)
            Image.new("RGBA", (20, 20), (0, 0, 255, 255)).save(btn_b)

            meta = make_animated_gif(
                bg,
                [
                    (btn_a, ["sway"], {"x": 10, "y": 20, "w": 30, "h": 30}),
                    (btn_b, ["rotate"], {"x": 150, "y": 150, "w": 20, "h": 20}),
                ],
                out,
            )
            self.assertEqual(set(meta["effects"]), {"sway", "rotate"})
            self.assertTrue(out.is_file())


    def test_foreground_layer_on_top(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            fg = tmp / "fg.png"
            out = tmp / "fg.gif"
            Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(bg)
            Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(btn)
            Image.new("RGBA", (30, 30), (0, 255, 0, 255)).save(fg)

            meta = make_animated_gif(
                bg,
                [(btn, ["breathing"], {"x": 80, "y": 80, "w": 40, "h": 40})],
                out,
                foreground_path=fg,
                foreground_layout={"x": 10, "y": 10, "w": 30, "h": 30},
            )
            self.assertTrue(meta["hasForeground"])
            with Image.open(out) as im:
                im.seek(0)
                px = im.convert("RGBA").load()
                self.assertEqual(px[25, 25][:3], (0, 255, 0))
                self.assertEqual(px[100, 100][:3], (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
