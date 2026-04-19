import asyncio
from autodev.graph import build_graph

def run_task(task_desc: str, session_id: str):
    graph = build_graph()
    state = {
        "session_id": session_id,
        "task": task_desc,
        "iterations": 0,
        "max_iterations": 5,
        "retry_history": [],
    }
    print(f"\n========== STARTING TASK: {task_desc} ==========")
    for event in graph.stream(state, {"recursion_limit": 50}):
        node_name = list(event.keys())[0]
        node_data = event[node_name]
        print(f"[{node_name}] -> current phase: {node_data.get('current_phase', 'unknown')}")
        if "error_classification" in node_data and node_name == "error_classifier":
            print(f"  --> ERROR: {node_data['error_classification']}")
        if "review_verdict" in node_data and node_name == "reviewer_agent":
            print(f"  --> VERDICT: {node_data['review_verdict']}")
            if node_data["review_verdict"] == "PASS":
                print("Task Succeeded!")
                break
    print(f"========== END TASK: {task_desc} ==========\n")

if __name__ == "__main__":
    tasks = [
        ("CLI calculator", "Build a python calculator that takes two numbers and an operator"),
        ("HTML portfolio", "Build a simple personal portfolio webpage in HTML with CSS"),
        ("Password manager", "Build a python console password manager that saves to json file")
    ]
    for _id, (name, prompt) in enumerate(tasks):
        run_task(prompt, f"test_session_{_id}")
