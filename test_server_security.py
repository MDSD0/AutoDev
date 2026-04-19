import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodev.server import RunFileRequest, run_file, serve_workspace_file
from autodev.session_memory import get_workspace_dir


class ServerPathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.session_id = f"test_safe_{uuid.uuid4().hex[:8]}"
        self.workspace = get_workspace_dir(self.session_id)

        self.safe_file = os.path.join(self.workspace, "main.py")
        with open(self.safe_file, "w", encoding="utf-8") as f:
            f.write("print('ok')\n")

        self.sibling_dir = self.workspace + "_evil"
        os.makedirs(self.sibling_dir, exist_ok=True)
        self.sibling_file = os.path.join(self.sibling_dir, "secret.txt")
        with open(self.sibling_file, "w", encoding="utf-8") as f:
            f.write("not allowed\n")

        fd, self.outside_file = tempfile.mkstemp(suffix=".py")
        os.close(fd)
        with open(self.outside_file, "w", encoding="utf-8") as f:
            f.write("print('outside')\n")

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)
        shutil.rmtree(self.sibling_dir, ignore_errors=True)
        if os.path.exists(self.outside_file):
            os.remove(self.outside_file)

    def test_workspace_file_allows_real_session_file(self):
        response = serve_workspace_file(self.session_id, "main.py")
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(os.path.realpath(response.path), os.path.realpath(self.safe_file))

    def test_workspace_file_blocks_sibling_prefix_traversal(self):
        attack_path = f"../{os.path.basename(self.sibling_dir)}/secret.txt"
        with self.assertRaises(HTTPException) as ctx:
            serve_workspace_file(self.session_id, attack_path)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_run_file_blocks_paths_outside_session_workspaces(self):
        with patch("autodev.server.launch_file") as launch_mock:
            with self.assertRaises(HTTPException) as ctx:
                run_file(RunFileRequest(filepath=self.outside_file))
        self.assertEqual(ctx.exception.status_code, 403)
        launch_mock.assert_not_called()

    def test_run_file_launches_session_workspace_files(self):
        with patch("autodev.server.launch_file", return_value=True) as launch_mock:
            response = run_file(RunFileRequest(filepath=self.safe_file))
        self.assertEqual(response["status"], "launched")
        self.assertEqual(os.path.realpath(response["filepath"]), os.path.realpath(self.safe_file))
        launch_mock.assert_called_once_with(os.path.realpath(self.safe_file))


if __name__ == "__main__":
    unittest.main()
