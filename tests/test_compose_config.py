import subprocess
import unittest
from pathlib import Path


class ComposeConfigTest(unittest.TestCase):
    def test_docker_compose_config_parses(self):
        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"docker compose config failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
