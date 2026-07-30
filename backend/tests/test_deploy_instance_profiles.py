import re
import unittest
from pathlib import Path


DEPLOY = (Path(__file__).resolve().parents[2] / "deploy.sh").read_text(encoding="utf-8")


class DeployInstanceProfileTests(unittest.TestCase):
    def test_remote_locks_xdt(self):
        self.assertIn('REMOTE_FIXED_PROJECT="小灯塔"', DEPLOY)
        self.assertRegex(
            DEPLOY,
            re.compile(r"cmd_remote\(\).*?apply_remote_xdt_profile", re.S),
        )

    def test_remote_hll_locks_hll_and_port(self):
        self.assertIn('REMOTE_FIXED_PROJECT="画啦啦"', DEPLOY)
        self.assertIn('REMOTE_HINT_PORT="${REMOTE_PORT_HLL}"', DEPLOY)
        self.assertRegex(
            DEPLOY,
            re.compile(r"cmd_remote_hll\(\).*?apply_remote_hll_profile", re.S),
        )

    def test_profiles_reuse_dot_env(self):
        self.assertNotIn('REMOTE_ENV_SRC="$ROOT_DIR/.env.hll"', DEPLOY)
        self.assertIn("--exclude '.env.hll'", DEPLOY)

    def test_instance_values_are_written_before_deploy(self):
        self.assertIn('remote_set_env_kv "FIXED_PROJECT"', DEPLOY)
        self.assertRegex(
            DEPLOY,
            re.compile(
                r"cmd_remote_deploy\(\).*?remote_apply_instance_env.*?remote_run_deploy",
                re.S,
            ),
        )


if __name__ == "__main__":
    unittest.main()
