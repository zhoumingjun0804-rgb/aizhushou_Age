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

    def test_animated_background_gif_preserves_frames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.gif"
            btn = tmp / "btn.png"
            out = tmp / "out.gif"
            frames = []
            for i in range(4):
                color = (40 * i, 80, 200 - 40 * i, 255)
                frames.append(Image.new("RGBA", (120, 80), color))
            frames[0].save(
                bg,
                save_all=True,
                append_images=frames[1:],
                duration=[80, 100, 120, 90],
                loop=0,
                disposal=2,
            )
            Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(btn)

            meta = make_animated_gif(
                bg,
                [(btn, ["float"], {"x": 10, "y": 10, "w": 20, "h": 20})],
                out,
                duration_sec=1.6,
            )
            self.assertTrue(meta["backgroundAnimated"])
            self.assertGreaterEqual(meta["frameCount"], 4)
            self.assertEqual(meta["effectDurationSec"], 1.6)
            self.assertTrue(out.is_file())
            with Image.open(out) as im:
                self.assertGreaterEqual(getattr(im, "n_frames", 1), 4)

    def test_effect_speed_independent_of_slow_background_gif(self):
        """慢速底图 GIF 时，按动效周期细分帧，上层动作不会跟着变慢。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "slow.gif"
            btn = tmp / "btn.png"
            out = tmp / "out.gif"
            frames = [
                Image.new("RGBA", (100, 100), (20 + i * 30, 20, 80, 255))
                for i in range(4)
            ]
            frames[0].save(
                bg,
                save_all=True,
                append_images=frames[1:],
                duration=[1000] * 4,  # 共 4 秒，仅 4 帧
                loop=0,
                disposal=2,
            )
            Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(btn)
            meta = make_animated_gif(
                bg,
                [(btn, ["float"], {"x": 10, "y": 10, "w": 20, "h": 20})],
                out,
                duration_sec=1.0,
                intensity="strong",
            )
            self.assertTrue(meta["backgroundAnimated"])
            self.assertEqual(meta["effectDurationSec"], 1.0)
            # 4 秒底图 + 1 秒动效周期，细分后应远多于原 4 帧（总帧有上限）
            self.assertGreaterEqual(meta["frameCount"], 8)
            self.assertLessEqual(meta["frameCount"], 36)
            self.assertAlmostEqual(meta["durationSec"], 4.0, places=1)
            self.assertTrue(out.is_file())

    def test_large_canvas_downscales_for_speed_and_size(self):
        """大底图会先限边再合成，体积更易控。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            out = tmp / "out.gif"
            Image.new("RGBA", (1242, 2208), (40, 50, 60, 255)).save(bg)
            Image.new("RGBA", (120, 60), (255, 80, 0, 255)).save(btn)
            meta = make_breathing_gif(
                bg, btn, out,
                button_x=500, button_y=1000, button_width=120, button_height=60,
                duration_sec=1.6,
                max_bytes=512 * 1024,
            )
            self.assertTrue(meta["underLimit"])
            self.assertLessEqual(max(meta["width"], meta["height"]), 720)
            self.assertLessEqual(meta["fileSize"], 512 * 1024)
            self.assertTrue(out.is_file())

    def test_layer_only_without_background(self):
        """无底图时，仅动效图层也可生成透明画布 GIF。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            btn = tmp / "btn.png"
            out = tmp / "out.gif"
            Image.new("RGBA", (80, 40), (255, 0, 0, 255)).save(btn)
            meta = make_animated_gif(
                None,
                [(btn, ["breathing"], None)],
                out,
                duration_sec=1.0,
                max_bytes=None,
            )
            self.assertGreaterEqual(meta["width"], 80)
            self.assertGreaterEqual(meta["height"], 40)
            self.assertFalse(meta["backgroundAnimated"])
            self.assertEqual(meta["effects"], ["breathing"])
            self.assertTrue(out.is_file())

    def test_max_bytes_compresses_large_gif(self):
        """大画布多帧时，开启 max_bytes 应压到上限内。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg = tmp / "bg.png"
            btn = tmp / "btn.png"
            out = tmp / "out.gif"
            img = Image.new("RGBA", (400, 500), (30, 40, 50, 255))
            px = img.load()
            for y in range(500):
                for x in range(0, 400, 4):
                    px[x, y] = ((x * 3 + y) % 255, (y * 2) % 255, (x + y) % 255, 255)
            img.save(bg)
            Image.new("RGBA", (80, 40), (255, 80, 0, 255)).save(btn)
            meta = make_breathing_gif(
                bg, btn, out,
                button_x=100, button_y=200, button_width=80, button_height=40,
                duration_sec=2.0,
                max_bytes=80 * 1024,
            )
            self.assertTrue(meta["underLimit"])
            self.assertLessEqual(meta["fileSize"], 80 * 1024)
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
