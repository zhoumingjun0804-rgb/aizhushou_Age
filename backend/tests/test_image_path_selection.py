import unittest

from app import _build_image_paths_from_selection


class TestImagePathSelection(unittest.TestCase):
    def test_empty_selection_does_not_auto_load_project_images(self):
        fields = {"selected_project_images": "[]"}
        paths = _build_image_paths_from_selection(fields, "小灯塔")
        self.assertEqual(paths, [])

    def test_missing_selection_does_not_auto_load(self):
        paths = _build_image_paths_from_selection({}, "小灯塔")
        self.assertEqual(paths, [])


if __name__ == "__main__":
    unittest.main()
