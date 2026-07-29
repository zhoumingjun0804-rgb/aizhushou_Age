import unittest
from pathlib import Path


class DeployRequirementsTests(unittest.TestCase):
    def test_pillow_is_bounded_for_centos7_manylinux2014(self):
        requirements = (
            Path(__file__).parents[1] / "requirements-deploy.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("Pillow<12", requirements.splitlines())

    def test_centos7_rembg_install_does_not_compile_playwright_greenlet(self):
        deploy_script = (
            Path(__file__).parents[2] / "deploy.sh"
        ).read_text(encoding="utf-8")

        self.assertNotIn('"greenlet>=3.1.1,<4"', deploy_script)


if __name__ == "__main__":
    unittest.main()
