import math
import unittest

from gpt_image_client import (
    GPT_IMAGE2_MIN_PIXELS,
    map_dimensions_to_gpt_image2_size,
    map_dimensions_to_size,
    resolve_gpt_api_size,
    resolve_gpt_image_output_quality,
)


class TestGptImage2Size(unittest.TestCase):
    def test_landing_head_maps_to_matching_aspect_not_landscape_preset(self):
        size = map_dimensions_to_gpt_image2_size(750, 560)
        w, h = [int(part) for part in size.split("x")]
        self.assertGreaterEqual(w * h, GPT_IMAGE2_MIN_PIXELS)
        self.assertEqual(w % 16, 0)
        self.assertEqual(h % 16, 0)
        target_ratio = 750 / 560
        self.assertLess(abs(math.log((w / h) / target_ratio)), 0.02)
        self.assertNotEqual(size, map_dimensions_to_size(750, 560))

    def test_square_keeps_1024(self):
        self.assertEqual(map_dimensions_to_gpt_image2_size(1024, 1024), "1024x1024")

    def test_online_size_1024x768_kept_exact(self):
        self.assertEqual(map_dimensions_to_gpt_image2_size(1024, 768), "1024x768")
        self.assertEqual(resolve_gpt_api_size(1024, 768, "gpt-image-2"), "1024x768")

    def test_resolve_api_size_uses_gpt_image2_mapping(self):
        self.assertEqual(resolve_gpt_api_size(750, 560, "gpt-image-2"), map_dimensions_to_gpt_image2_size(750, 560))
        self.assertEqual(resolve_gpt_api_size(750, 560, "gpt-image-1.5"), map_dimensions_to_size(750, 560))


class TestGptImage2Quality(unittest.TestCase):
    def test_default_quality_is_medium_for_gpt_image2(self):
        self.assertEqual(resolve_gpt_image_output_quality("gpt-image-2"), "medium")

    def test_override_quality(self):
        self.assertEqual(resolve_gpt_image_output_quality("gpt-image-2", override="high"), "high")
        self.assertEqual(resolve_gpt_image_output_quality("gpt-image-2", override="low"), "low")

    def test_other_models_skip_quality(self):
        self.assertIsNone(resolve_gpt_image_output_quality("gpt-image-1.5"))


if __name__ == "__main__":
    unittest.main()
