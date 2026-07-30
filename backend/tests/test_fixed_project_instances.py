import os
import unittest
from unittest.mock import patch

import app
from project_auth import fixed_project


class FixedProjectConfigTests(unittest.TestCase):
    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_fixed_project_accepts_known_project(self):
        self.assertEqual(fixed_project(), "画啦啦")

    @patch.dict(os.environ, {"FIXED_PROJECT": "未知项目"}, clear=False)
    def test_fixed_project_rejects_unknown_project(self):
        self.assertIsNone(fixed_project())

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_html_uses_fixed_project_brand_and_hides_picker(self):
        html = (
            "<title>__PAGE_TITLE__</title>"
            "<h1>__PAGE_BRAND__</h1>"
            '<div class="shared-project-card__PROJECT_CARD_EXTRA__"></div>'
        )
        rendered = app._inject_instance_flags(html)
        self.assertIn("<title>A-智绘 · 画啦啦</title>", rendered)
        self.assertIn("<h1>🎨 A-智绘 · 画啦啦</h1>", rendered)
        self.assertIn("shared-project-card feature-hidden", rendered)

    @patch.dict(os.environ, {"FIXED_PROJECT": ""}, clear=False)
    def test_html_keeps_default_brand_without_fixed_project(self):
        html = (
            "<title>__PAGE_TITLE__</title>"
            "<h1>__PAGE_BRAND__</h1>"
            '<div class="shared-project-card__PROJECT_CARD_EXTRA__"></div>'
        )
        rendered = app._inject_instance_flags(html)
        self.assertIn("<title>A-智绘</title>", rendered)
        self.assertIn("<h1>🎨 A-智绘</h1>", rendered)
        self.assertIn('class="shared-project-card"', rendered)
        self.assertNotIn("feature-hidden", rendered)


class FixedProjectRequestTests(unittest.TestCase):
    def make_handler(self):
        handler = app.Handler.__new__(app.Handler)
        handler.sent = None
        handler._send_json = lambda payload, status=200: setattr(
            handler, "sent", (payload, status)
        )
        return handler

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_matching_project_is_allowed(self):
        handler = self.make_handler()
        self.assertEqual(handler._auth_project("画啦啦"), "画啦啦")
        self.assertIsNone(handler.sent)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_cross_project_request_is_forbidden(self):
        handler = self.make_handler()
        self.assertIsNone(handler._auth_project("小灯塔"))
        self.assertIsNotNone(handler.sent)
        payload, status = handler.sent
        self.assertEqual(status, 403)
        self.assertIn("画啦啦", payload.get("error", ""))

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_empty_auth_project_returns_fixed(self):
        handler = self.make_handler()
        self.assertEqual(handler._auth_project(""), "画啦啦")
        self.assertIsNone(handler.sent)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_missing_project_resolves_to_fixed_project(self):
        handler = self.make_handler()
        handler._query_params = lambda: {}
        self.assertEqual(handler._resolve_project_for_request(""), "画啦啦")
        self.assertIsNone(handler.sent)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_explicit_mismatch_does_not_silently_change_project(self):
        handler = self.make_handler()
        self.assertIsNone(handler._resolve_project_for_request("小灯塔"))
        self.assertIsNotNone(handler.sent)
        self.assertEqual(handler.sent[1], 403)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_filter_keeps_only_fixed_project_items(self):
        items = [{"project": "小灯塔"}, {"project": "画啦啦"}]
        self.assertEqual(
            app._filter_for_fixed_project(items),
            [{"project": "画啦啦"}],
        )
        self.assertEqual(items, [{"project": "小灯塔"}, {"project": "画啦啦"}])

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_filter_by_name_key(self):
        items = [{"name": "小灯塔"}, {"name": "画啦啦"}]
        self.assertEqual(
            app._filter_for_fixed_project(items, key="name"),
            [{"name": "画啦啦"}],
        )

    @patch.dict(os.environ, {"FIXED_PROJECT": ""}, clear=False)
    def test_filter_unchanged_without_fixed_project(self):
        items = [{"project": "小灯塔"}, {"project": "画啦啦"}]
        self.assertIs(app._filter_for_fixed_project(items), items)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    @patch("app.list_projects")
    def test_projects_endpoint_returns_only_fixed(self, mock_list):
        mock_list.return_value = [
            {"name": "小灯塔", "product_type": "xdt"},
            {"name": "画啦啦", "product_type": "hll"},
        ]
        handler = self.make_handler()
        handler.path = "/projects"
        handler.do_GET()
        payload, status = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["projects"],
            [{"name": "画啦啦", "product_type": "hll"}],
        )

    @patch.dict(os.environ, {"FIXED_PROJECT": ""}, clear=False)
    @patch("app.is_gate_enabled", return_value=True)
    @patch("app.list_projects")
    def test_projects_endpoint_gate_behavior_when_not_fixed(self, mock_list, _gate):
        mock_list.return_value = [
            {"name": "小灯塔"},
            {"name": "画啦啦"},
        ]
        handler = self.make_handler()
        handler.path = "/projects"
        handler._token_project = lambda: "小灯塔"
        handler.do_GET()
        payload, status = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["projects"], [{"name": "小灯塔"}])

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    @patch("app.load_history")
    @patch("app.filter_history_items", side_effect=lambda items: items)
    def test_history_endpoint_returns_only_fixed(self, _filter, mock_load):
        mock_load.return_value = [
            {"project": "小灯塔", "id": "1"},
            {"project": "画啦啦", "id": "2"},
        ]
        handler = self.make_handler()
        handler.path = "/history"
        handler.do_GET()
        payload, status = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [{"project": "画啦啦", "id": "2"}])

    @patch.dict(os.environ, {"FIXED_PROJECT": ""}, clear=False)
    @patch("app.is_gate_enabled", return_value=True)
    @patch("app.load_history")
    @patch("app.filter_history_items", side_effect=lambda items: items)
    def test_history_endpoint_gate_behavior_when_not_fixed(
        self, _filter, mock_load, _gate
    ):
        mock_load.return_value = [
            {"project": "小灯塔", "id": "1"},
            {"project": "画啦啦", "id": "2"},
        ]
        handler = self.make_handler()
        handler.path = "/history"
        handler._token_project = lambda: "小灯塔"
        handler.do_GET()
        payload, status = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [{"project": "小灯塔", "id": "1"}])

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    @patch("app.load_output_sizes", return_value=[{"w": 1, "h": 1}])
    def test_output_sizes_without_project_uses_fixed_product_type(self, _sizes):
        handler = self.make_handler()
        handler.path = "/api/output-sizes"
        handler.do_GET()
        payload, status = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["type"], "hll")

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_output_sizes_explicit_mismatch_forbidden(self):
        handler = self.make_handler()
        handler.path = "/api/output-sizes?project=%E5%B0%8F%E7%81%AF%E5%A1%94"
        handler.do_GET()
        self.assertIsNotNone(handler.sent)
        self.assertEqual(handler.sent[1], 403)

    @patch.dict(os.environ, {"FIXED_PROJECT": "画啦啦"}, clear=False)
    def test_design_types_explicit_mismatch_forbidden(self):
        handler = self.make_handler()
        handler.path = "/api/design-types?project=%E5%B0%8F%E7%81%AF%E5%A1%94"
        handler.do_GET()
        self.assertIsNotNone(handler.sent)
        self.assertEqual(handler.sent[1], 403)


if __name__ == "__main__":
    unittest.main()
