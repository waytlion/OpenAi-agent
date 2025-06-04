#API_URL = "http://localhost:8081/task/index/"  # API endpoint for SWE-Bench-Lite
#LOG_FILE = "results.log"

import openai
import asyncio
import os
from pathlib import Path
from openai import AsyncOpenAI
import requests
import json
import subprocess
import re
import shutil
import stat

from agents import (
    Agent,
    Runner,
    function_tool,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)

def get_project_root() -> Path:
    """Get the project root directory"""
    return Path(__file__).resolve().parent


PROJECT_ROOT = get_project_root()
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"

BASE_URL = os.getenv("EXAMPLE_BASE_URL") or "http://localhost:11434/v1"
API_KEY = os.getenv("EXAMPLE_API_KEY") or "ollama"
MODEL_NAME = os.getenv("EXAMPLE_MODEL_NAME") or "gemma3:1b"


client = AsyncOpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
)
set_default_openai_client(client=client, use_for_tracing=False)
set_default_openai_api("chat_completions")
set_tracing_disabled(disabled=True)

API_URL = "http://localhost:8081/task/index/"  # API endpoint for SWE-Bench-Lite
LOG_FILE = "results.log"



def on_rm_error(func, path, exc_info):
    # Change the file to be writable and try again
    os.chmod(path, stat.S_IWRITE)
    func(path)

async def handle_task(index):

    @function_tool
    def read_file(file_path: str) -> str:
        """Read the contents of a file."""
        full_path = os.path.join(repo_dir, file_path)
        print(f"DEBUG: Attempting to read file: {full_path}")  
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"DEBUG: Successfully read {len(content)} characters")  
        return content

    @function_tool
    def write_file(file_path: str, content: str) -> str:
        """Write content to a file."""
        full_path = os.path.join(repo_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"File {file_path} written successfully"
    
    api_url = f"{API_URL}{index}"
    print(f"Fetching test case {index} from {api_url}...")
    repo_dir = os.path.join(WORKSPACE_ROOT,"repos", f"repo_{index}")
    start_dir = os.getcwd()

    try:
        response = requests.get(api_url)
        if response.status_code != 200:
            raise Exception(f"Invalid response: {response.status_code}")

        testcase = response.json()
        prompt = testcase["Problem_statement"]
        git_clone = testcase["git_clone"]
        fail_tests = json.loads(testcase.get("FAIL_TO_PASS", "[]"))
        pass_tests = json.loads(testcase.get("PASS_TO_PASS", "[]"))
        instance_id = testcase["instance_id"]

        # Extract repo URL and commit hash
        parts = git_clone.split("&&")
        clone_part = parts[0].strip()
        checkout_part = parts[-1].strip() if len(parts) > 1 else None

        repo_url = clone_part.split()[2]

        print(f"Cloning repository {repo_url} into {repo_dir}...")
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        subprocess.run(["git", "clone", repo_url, repo_dir], check=True, env=env)

        if checkout_part:
            commit_hash = checkout_part.split()[-1]
            print(f"Checking out commit: {commit_hash}")
            subprocess.run(["git", "checkout", commit_hash], cwd=repo_dir, check=True, env=env)

        # Build full prompt for the agent
        full_prompt = (
            f"You are a team of agents with the following roles:\n"
            f"- Planner: breaks down the problem into coding tasks\n"
            f"- Coder: makes actual changes to the code files in the Git repository\n"
            f"- Tester: runs the test suite and checks whether the bug is resolved\n\n"
            f"Work in the directory: repo_{index}. This is a Git repository.\n"
            f"Your goal is to fix the problem described below.\n"
            f"All code changes must be saved to the files, so they appear in `git diff`.\n"
            f"The fix will be verified by running the affected tests.\n\n"
            f"Problem description:\n"
            f"{prompt}\n\n"
            f"Make sure the fix is minimal and only touches what's necessary to resolve the failing tests."
        )
        print(f"Launching agent (OpenAI)...")
        instructions = "you're a coding assistant. You can inspect source files, modify code, and fix failing tests. Use the provided tools to read/write files and run tests."
        agent = Agent(
            name="Agent",
            instructions=instructions,
            model=MODEL_NAME,
            #tools=[read_file, write_file] 
        )
        result = await Runner.run(agent, full_prompt)
        print("Agent final output:", result.final_output)

        # Token usage
        token_total = extract_last_token_total_from_logs()

        # Check for changes in the repository BEFORE calling REST service
        print("Checking for changes in the repository...")
        result = subprocess.run(
            ["git", "diff", "--exit-code"],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode == 0:
            print("No changes detected in the repository. Skipping evaluation.")
            # Log this to results.log
            os.chdir(start_dir)
            with open(LOG_FILE, "a", encoding="utf-8") as log:
                log.write(f"\n--- TESTCASE {index} ---\n")
                log.write("No changes detected in the repository. Skipping evaluation.\n")
                log.write(f"Total Tokens Used: {token_total}\n")
            return  # Exit the function if no changes are detected
        else:
            print("Changes detected in the repository. Proceeding with evaluation.")

        # Call REST service only if changes were detected
        print(f"Calling SWE-Bench REST service with repo: {repo_dir}")
        test_payload = {
            "instance_id": instance_id,
            "repoDir": f"/repos/repo_{index}",  # mount with docker
            "FAIL_TO_PASS": fail_tests,
            "PASS_TO_PASS": pass_tests
        }
        print("Test payload:", json.dumps(test_payload, indent=4))  # print the payload
        res = requests.post("http://localhost:8082/test", json=test_payload)
        res.raise_for_status()
        print("REST response: (res.text)", res.text) 
        print("Full REST response (res.json()):", res.json()) 
        result_raw = res.json().get("harnessOutput", "{}")
        result_json = json.loads(result_raw)
        if not result_json:
            raise ValueError("No data in harnessOutput – possible evaluation error or empty result")
        instance_id = next(iter(result_json))
        tests_status = result_json[instance_id]["tests_status"]
        fail_pass_results = tests_status["FAIL_TO_PASS"]
        fail_pass_total = len(fail_pass_results["success"]) + len(fail_pass_results["failure"])
        fail_pass_passed = len(fail_pass_results["success"])
        pass_pass_results = tests_status["PASS_TO_PASS"]
        pass_pass_total = len(pass_pass_results["success"]) + len(pass_pass_results["failure"])
        pass_pass_passed = len(pass_pass_results["success"])

        # Log results
        os.chdir(start_dir)
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"\n--- TESTCASE {index} ---\n")
            log.write(f"FAIL_TO_PASS passed: {fail_pass_passed}/{fail_pass_total}\n")
            log.write(f"PASS_TO_PASS passed: {pass_pass_passed}/{pass_pass_total}\n")
            log.write(f"Total Tokens Used: {token_total}\n")
        print(f"Test case {index} completed and logged.")

    except Exception as e:
        os.chdir(start_dir)
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"\n--- TESTCASE {index} ---\n")
            log.write(f"Error: {e}\n")
        print(f"Error in test case {index}: {e}")


def extract_last_token_total_from_logs():
    log_dir = r"logs"
    log_files = [f for f in os.listdir(log_dir) if f.endswith(".log")]
    if not log_files:
        return "No logs found"

    log_files.sort(reverse=True)

    latest_log_path = os.path.join(log_dir, log_files[0])
    with open(latest_log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in reversed(lines):
        match = re.search(r'Cumulative Total=(\d+)', line)
        if match:
            return int(match.group(1))

    return "Cumulative Total not found"


async def main():
    for i in range(1, 2):
        await handle_task(i)

if __name__ == "__main__":
    asyncio.run(main())
