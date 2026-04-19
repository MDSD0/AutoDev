"""
interactive_cli.py — AutoDev human-in-the-loop CLI driver.
Runs the workflow and intercepts human_gate pauses for interactive resumption.
"""
import sys
import os
import uuid
import json

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

from autodev.graph import stream_task, continue_task
from autodev.session_memory import load_state

def run_cli_test(task: str, session_id: str):
    print(f"\n{'='*80}\nTASK: {task}\n{'='*80}\n")
    
    # Run stream
    generator = stream_task(
        task=task,
        session_id=session_id,
        provider="auto",
        refine_prompt=True,
        max_retries=6,
    )
    
    _process_stream(generator, session_id)

def _process_stream(generator, session_id):
    while True:
        try:
            for event in generator:
                node = event.get("node")
                data = event.get("data", {})
                
                if node == "__error__":
                    print(f"\nFATAL ERROR: {data.get('error')}")
                    return
                print(node)
                
            state = load_state(session_id)
            if not state:
                print("No state found.")
                break
                
            status = state.get("status", "")
            
            if status == "success":
                print("\nTask completed successfully!")
                ec = state.get("exec_output", "(no output)")
                print(f"Exec output preview: {ec[:200]}")
                rubric = state.get("review_rubric", {})
                print(f"Scores: {rubric.get('scores', rubric)}")
                break
                
            elif status == "awaiting_user":
                print(f"\nHUMAN ENTERS LOOP (awaiting_user). Reason:")
                print(state.get("human_gate_reason", "Convergence/Retry exhaustion."))
                # Intercept
                user_feedback = input("\nEnter feedback to guide the agent (or 'exit' to abort): ")
                if user_feedback.lower() == "exit":
                    print("Aborting.")
                    break
                    
                print("\nResuming session...")
                generator = continue_task(
                    session_id=session_id,
                    user_guidance=user_feedback,
                    provider="auto",
                    max_retries=3,
                )
            else:
                print(f"\nTerminated with status: {status}")
                break
                
        except StopIteration:
            break
        except Exception as e:
            print(f"Loop crashed: {e}")
            break

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, required=True, help="Task to execute")
    args = parser.parse_args()
    
    run_cli_test(args.task, str(uuid.uuid4()))
