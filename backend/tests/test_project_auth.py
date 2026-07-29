import os
import unittest
from unittest.mock import patch

from project_auth import project_slug, fixed_project, ALLOWED_PROJECTS


class ProjectAuthTests(unittest.TestCase):
    def test_project_slug(self):
        self.assertEqual(project_slug("画啦啦"), "HLL")
        self.assertEqual(project_slug("小灯塔"), "XDT")

    def test_allowed_projects(self):
        self.assertEqual(set(ALLOWED_PROJECTS), {"画啦啦", "小灯塔"})

    @patch.dict(os.environ, {"FIXED_PROJECT": "小灯塔"}, clear=False)
    def test_fixed_project_xdt(self):
        self.assertEqual(fixed_project(), "小灯塔")

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_fixed_project_hll(self):
        self.assertEqual(fixed_project(), "画啦啦")

    @patch.dict(os.environ, {"FIXED_PROJECT": "不存在"}, clear=False)
    def test_fixed_project_invalid(self):
        self.assertIsNone(fixed_project())

    @patch.dict(os.environ, {"FIXED_PROJECT": ""}, clear=False)
    def test_fixed_project_empty(self):
        self.assertIsNone(fixed_project())


if __name__ == "__main__":
    unittest.main()
