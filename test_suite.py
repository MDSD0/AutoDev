import unittest
import os
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodev.config import Config
from autodev.llm_utils import validate_llm_json, get_llm
from autodev.spec_extractor import extract_spec
from autodev.planner import create_plan
from autodev.dependency_manager import detect_imports, imports_to_packages
from autodev.executor import execute_project
from autodev.quality_reviewer import review_quality
from autodev.error_classifier import classify_error
from autodev.session_memory import save_state, load_state, SessionRAG

class TestAutoDev(unittest.TestCase):
    def setUp(self):
        self.provider = "auto"
        self.session_id = "test_ses_" + str(int(time.time()))
        
    def test_1_json_repair(self):
        llm = get_llm(self.provider)
        bad_json = """Here is your json:
        ```json
        {
            "problem_statement": "calculator",
            "output_type": "python",
            "expected_files": ["main.py"],
            "entrypoint": "main.py",
        }
        ```
        """
        required_keys = ["output_type"]
        # It should strip codeblocks and trailing commas
        res, err = validate_llm_json(bad_json, required_keys, llm[0])
        self.assertIn("output_type", res)
        self.assertEqual(res["output_type"], "python")

    def test_2_spec_extraction(self):
        prompt = "Build a python calculator"
        llm = get_llm(self.provider)
        spec, _ = extract_spec(prompt, llm[0])
        self.assertIn("output_type", spec)
        self.assertIn("expected_files", spec)
        self.assertIn("entrypoint", spec)
        
    def test_3_plan_generation(self):
        spec = {"output_type": "python", "expected_files": ["calc.py"], "entrypoint": "calc.py"}
        llm = get_llm(self.provider)
        plan, _ = create_plan(spec, llm[0])
        self.assertIn("file_order", plan)
        self.assertIn("packages", plan)

    def test_4_dep_installer(self):
        # We write some test imports
        files = {"main.py": "import requests\nimport numpy as np\n"}
        imports = detect_imports(files["main.py"])
        self.assertIn("requests", imports)
        self.assertIn("numpy", imports)
        
        pkgs = imports_to_packages(imports)
        self.assertIn("requests", pkgs)
        self.assertIn("numpy", pkgs)

    def test_5_executor(self):
        files = {"main.py": "print('hello world')"}
        ws = os.path.join(Config.WORK_DIR, 'test_exec_ws')
        if not os.path.exists(ws):
            os.makedirs(ws)
        report = execute_project(files, "main.py", "python", ws)
        self.assertTrue(report["success"])
        self.assertEqual(report["error_type"], "none")
        self.assertIn("hello world", report["output"])

    def test_6_quality_reviewer(self):
        spec = {"output_type": "python", "expected_files": ["main.py"], "entrypoint": "main.py"}
        plan = {"file_order": ["main.py"]}
        files = {"main.py": "print('hello world')"}
        exec_report = {"success": True, "error_type": "none", "output": "hello world"}
        
        # Test just the call to make sure the LLM interaction doesn't raise exception
        llm = get_llm(self.provider)
        review, raw = review_quality(spec, plan, files, exec_report, llm[0], retry_history=[])
        self.assertIn(review.get("verdict"), ["PASS", "RETRY"])
        if review.get("verdict") == "PASS":
            # All scores should theoretically be good, but check loosely
            self.assertGreaterEqual(review.get("runtime_correctness", 0), 1)

    def test_7_error_classifier(self):
        exec_report = {"success": False, "error_type": "syntax", "error_summary": "invalid syntax", "stderr": "SyntaxError: invalid syntax in main.py"}
        files = {"main.py": "x="}
        spec = {"output_type": "python"}
        
        classification = classify_error(exec_report, files, spec)
        self.assertEqual(classification.error_type, "syntax")
        self.assertEqual(classification.suggested_strategy, "syntax_fix")
        
    def test_8_session_memory(self):
        sid = "test_rag_" + str(int(time.time()))
        
        # Test state save/load
        state = {"session_id": sid, "task": "hello"}
        save_state(sid, state)
        loaded = load_state(sid)
        self.assertEqual(loaded["task"], "hello")
        
        # Test RAG isolation
        rag = SessionRAG()
        rag.add_memory(sid, "Memory A", {"type": "test"})
        res = rag.retrieve(sid, "Memory A", limit=1)
        self.assertIn("Memory A", res)
        
        # Other session shouldn't see it
        sid2 = sid + "_2"
        res2 = rag.retrieve(sid2, "Memory A", limit=1)
        self.assertNotIn("Memory A", res2)

if __name__ == "__main__":
    unittest.main()
