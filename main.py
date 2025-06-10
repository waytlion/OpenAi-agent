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
import sys

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

#! Das hier ist Ollama
#BASE_URL = os.getenv("EXAMPLE_BASE_URL") or "http://localhost:11434/v1"
#API_KEY = os.getenv("EXAMPLE_API_KEY") or "ollama"
#MODEL_NAME = os.getenv("EXAMPLE_MODEL_NAME") or "gemma3:1b"

#! Das hier ist LittleLLM
BASE_URL = os.getenv("EXAMPLE_BASE_URL") or "http://188.245.32.59:4000"
API_KEY = os.getenv("EXAMPLE_API_KEY") or "sk-6uV8zFo9OcPqgMD5R4Bb3g"
MODEL_NAME = os.getenv("EXAMPLE_MODEL_NAME") or "gpt-4o"

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
    # Track file reads to prevent infinite loops
    file_read_count = {}
    max_reads_per_file = 3

    @function_tool
    def read_file(file_path: str) -> str:
        """Read the contents of a file."""
        # Remove any leading repo_X/ prefix if present
        if file_path.startswith(f"repo_{index}/"):
            file_path = file_path[len(f"repo_{index}/"):]

        # Circuit breaker - prevent excessive reads of same file
        if file_path in file_read_count:
            file_read_count[file_path] += 1
            if file_read_count[file_path] > max_reads_per_file:
                return f"Error: File {file_path} has been read {max_reads_per_file} times already. Refusing to read again to prevent infinite loops."
        else:
            file_read_count[file_path] = 1

        full_path = os.path.join(repo_dir, file_path)
        print(f"DEBUG: Attempting to read file: {full_path} (read #{file_read_count[file_path]})")

        # Check if file exists before trying to read
        if not os.path.exists(full_path):
            return f"Error: File {file_path} does not exist"

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            print(f"DEBUG: Successfully read {len(content)} characters")
            return content
        except UnicodeDecodeError:
            return f"Error: File {file_path} appears to be a binary file and cannot be read as text"
        except Exception as e:
            return f"Error reading file {file_path}: {str(e)}"

    @function_tool
    def write_file(file_path: str, content: str) -> str:
        """Write content to a file."""
        # Remove any leading repo_X/ prefix if present
        if file_path.startswith(f"repo_{index}/"):
            file_path = file_path[len(f"repo_{index}/"):]

        full_path = os.path.join(repo_dir, file_path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"WRITE: Agent wrote {len(content)} characters to {file_path}")
            return f"File {file_path} written successfully"
        except Exception as e:
            print(f"ERROR: Failed to write {file_path}: {str(e)}")
            return f"Error writing file {file_path}: {str(e)}"

    @function_tool
    def list_files(directory_path: str = ".") -> str:
        """List files and directories in the given path."""
        if directory_path.startswith(f"repo_{index}/"):
            directory_path = directory_path[len(f"repo_{index}/"):]

        full_path = os.path.join(repo_dir, directory_path)

        if not os.path.exists(full_path):
            return f"Error: Directory {directory_path} does not exist"

        try:
            items = []
            for item in sorted(os.listdir(full_path)):
                item_path = os.path.join(full_path, item)
                if os.path.isdir(item_path):
                    items.append(f"📁 {item}/")
                else:
                    items.append(f"📄 {item}")
            return "\n".join(items)
        except Exception as e:
            return f"Error listing directory {directory_path}: {str(e)}"

    @function_tool
    def run_specific_tests(test_paths: str) -> str:
        """Run specific tests to see their output."""
        try:
            # Use the Python executable from the current virtual environment
            python_executable = sys.executable  # This points to the current venv's Python
            result = subprocess.run(
                [python_executable, "-m", "pytest", "-xvs"] + test_paths.split(),
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=120
            )
            output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                output += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                output += f"STDERR:\n{result.stderr}\n"
            return output
        except Exception as e:
            return f"Error running tests: {str(e)}"

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
            f"Work efficiently and avoid unnecessary file operations."
        )
        print(f"Launching agent (OpenAI)...")
        instructions = (
            "You are a bug-fixing specialist. Follow this workflow:\n\n"
            "1. Use list_files() to explore the repository structure.\n"
            "2. Read the relevant source files mentioned in the problem.\n"
            "3. Analyze the code to identify the bug.\n"
            "4. Write the minimal fix using write_file().\n"
            "5. Do not spend more than 2 turns analyzing the problem before making a change.\n\n"
            "IMPORTANT: Always make a code change, even if you cannot validate it with tests."
        )
        agent = Agent(
            name="Agent",
            instructions=instructions,
            model=MODEL_NAME,
            tools=[read_file, write_file, list_files, run_specific_tests],
        )
        result = await Runner.run(agent, full_prompt, max_turns = 5)
        print("Agent final output:", result.final_output)
        print(result)

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


async def main():
    for i in range(20, 31):
        await handle_task(i)
if __name__ == "__main__":
    print(f"DEBUG: Using Python executable: {sys.executable}")
    asyncio.run(main())
