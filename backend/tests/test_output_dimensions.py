import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import finalize_generation_output, resolve_output_dimensions
from gpt_image_client import map_dimensions_to_gpt_image2_size, map_dimensions_to_size


class TestMapDimensionsToSize(unittest.TestCase):
    def test_wide_custom_size_maps_to_landscape_preset(self):
        self.assertEqual(map_dimensions_to_size(702, 320), "1536x1024")

    def test_tall_custom_size_maps_to_portrait_preset(self):
        self.assertEqual(map_dimensions_to_size(750, 1334), "1024x1792")

    def test_square_maps_to_square_preset(self):
        self.assertEqual(map_dimensions_to_size(1000, 1000), "1024x1024")


class TestResolveOutputDimensions(unittest.TestCase):
    def test_explicit_pixels_use_actual_aspect_ratio(self):
        ratio, w, h = resolve_output_dimensions("16:9", 702, 320)
        self.assertEqual((w, h), (702, 320))
        self.assertEqual(ratio, "351:160")

    def test_wide_banner_without_pixels_falls_back_to_square(self):
        # 继续编辑若未传 output_width/height，超宽比会落到默认方图，再裁回原尺寸就会丢内容
        ratio, w, h = resolve_output_dimensions("3:1")
        self.assertEqual(ratio, "3:1")
        self.assertEqual((w, h), (1024, 1024))

    def test_wide_banner_with_pixels_keeps_original_size(self):
        ratio, w, h = resolve_output_dimensions("3:1", 1920, 640)
        self.assertEqual((w, h), (1920, 640))
        self.assertEqual(ratio, "3:1")


class TestFinalizeGenerationOutput(unittest.TestCase):
    def test_resizes_downloaded_image_to_target_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variant.png"
            Image.new("RGB", (1024, 1024), color=(0, 255, 0)).save(path)
            finalize_generation_output(path, 702, 320)
            with Image.open(path) as img:
                self.assertEqual(img.size, (702, 320))

    def test_matching_aspect_uses_direct_resize_not_crop(self):
        api_size = map_dimensions_to_gpt_image2_size(750, 560)
        aw, ah = [int(part) for part in api_size.split("x")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variant.png"
            Image.new("RGB", (aw, ah), color=(255, 0, 0)).save(path)
            finalize_generation_output(path, 750, 560)
            with Image.open(path) as img:
                self.assertEqual(img.size, (750, 560))

    def test_mismatched_aspect_uses_letterbox_without_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variant.png"
            Image.new("RGB", (1024, 1024), color=(0, 0, 255)).save(path)
            finalize_generation_output(path, 750, 560)
            with Image.open(path) as img:
                self.assertEqual(img.size, (750, 560))
                corners = [img.getpixel((0, 0)), img.getpixel((749, 0)), img.getpixel((0, 559)), img.getpixel((749, 559))]
                self.assertTrue(all(px == (0, 0, 0) for px in corners))
                self.assertEqual(img.getpixel((375, 280)), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()
