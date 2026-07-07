import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import finalize_generation_output, resolve_output_dimensions
from gpt_image_client import map_dimensions_to_size


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


class TestFinalizeGenerationOutput(unittest.TestCase):
    def test_resizes_downloaded_image_to_target_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variant.png"
            Image.new("RGB", (1024, 1024), color=(0, 255, 0)).save(path)
            finalize_generation_output(path, 702, 320)
            with Image.open(path) as img:
                self.assertEqual(img.size, (702, 320))


if __name__ == "__main__":
    unittest.main()
