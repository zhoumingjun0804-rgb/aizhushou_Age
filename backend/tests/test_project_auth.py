import os
import unittest
from unittest.mock import patch

from project_auth import unlock, resolve_token, project_slug, ALLOWED_PROJECTS, is_gate_enabled


class ProjectAuthTests(unittest.TestCase):
    def setUp(self):
        import project_auth

        project_auth._tokens.clear()

    def test_project_slug(self):
        self.assertEqual(project_slug("画啦啦"), "HLL")
        self.assertEqual(project_slug("小灯塔"), "XDT")

    @patch.dict(os.environ, {"PROJECT_PASSWORD_HLL": "secret-hll"}, clear=False)
    def test_unlock_success(self):
        token = unlock("画啦啦", "secret-hll")
        self.assertTrue(token)
        info = resolve_token(token)
        self.assertEqual(info["project"], "画啦啦")

    @patch.dict(os.environ, {"PROJECT_PASSWORD_HLL": "secret-hll"}, clear=False)
    def test_unlock_wrong_password(self):
        self.assertIsNone(unlock("画啦啦", "wrong"))

    def test_unlock_unknown_project(self):
        self.assertIsNone(unlock("不存在", "x"))

    @patch.dict(os.environ, {"PROJECT_GATE_ENABLED": "0"}, clear=False)
    def test_gate_disabled(self):
        self.assertFalse(is_gate_enabled())

    @patch.dict(os.environ, {"PROJECT_GATE_ENABLED": "1"}, clear=False)
    def test_gate_enabled_default(self):
        self.assertTrue(is_gate_enabled())
