import unittest
from unittest import mock

from app import build_edit_chain, build_history_entry


class TestEditHistoryLineage(unittest.TestCase):
    def test_edit_entry_keeps_root_prompt_and_base_image(self):
        entry = build_history_entry(
            mode="edit",
            prompt="原始长 Prompt：海上生明月",
            description="修改色调为蓝色。在现有图片基础上修改…",
            source="edit",
            project="画啦啦",
            output_image="edit_output_abc.png",
            edit_type="迭代修改",
            edit_prompt="[迭代修改] 修改色调为蓝色…",
            parent_id="deadbeef",
            root_prompt="原始长 Prompt：海上生明月",
            root_image="variant_root.png",
            base_image="variant_root.png",
            edit_chain=[
                {"text": "修改色调为蓝色。在现有图片基础上修改…", "id": "deadbeef"},
            ],
        )
        self.assertEqual(entry["parent_id"], "deadbeef")
        self.assertEqual(entry["root_prompt"], "原始长 Prompt：海上生明月")
        self.assertEqual(entry["root_image"], "variant_root.png")
        self.assertEqual(entry["base_image"], "variant_root.png")
        self.assertEqual(entry["prompt"], "原始长 Prompt：海上生明月")
        self.assertEqual(len(entry["edit_chain"]), 1)

    def test_build_edit_chain_walks_parents_without_stored_chain(self):
        parent = {
            "id": "p2",
            "mode": "edit",
            "description": "改成蓝色",
            "parent_id": "p1",
            "output_image": "blue.png",
        }
        grand = {
            "id": "p1",
            "mode": "edit",
            "description": "先改构图",
            "parent_id": "",
            "output_image": "layout.png",
        }

        def _find(hid):
            return {"p1": grand, "p2": parent}.get(hid)

        with mock.patch("app.find_history_entry", side_effect=_find):
            chain = build_edit_chain(
                parent_id="p2",
                current_description="再改成黄色",
                current_output="yellow.png",
                current_id="p3",
            )
        self.assertEqual([c["text"] for c in chain], ["先改构图", "改成蓝色", "再改成黄色"])
        self.assertEqual(chain[-1]["output_image"], "yellow.png")

    def test_build_edit_chain_uses_parent_edit_chain_when_present(self):
        parent = {
            "id": "p2",
            "mode": "edit",
            "description": "改成蓝色",
            "edit_chain": [
                {"text": "先改构图"},
                {"text": "改成蓝色"},
            ],
        }
        with mock.patch("app.find_history_entry", return_value=parent):
            chain = build_edit_chain(
                parent_id="p2",
                current_description="再改成黄色",
            )
        self.assertEqual([c["text"] for c in chain], ["先改构图", "改成蓝色", "再改成黄色"])


if __name__ == "__main__":
    unittest.main()
