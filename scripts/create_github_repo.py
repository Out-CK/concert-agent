"""
One-time setup script: creates the concert-agent repo in the OUT-CK GitHub org
and pushes the local main branch to it.

Usage:
    python scripts/create_github_repo.py
"""
import os
import subprocess
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "OUT-CK")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "concert-agent")

if not GITHUB_TOKEN:
    print("ERROR: GITHUB_TOKEN is not set in your environment.", file=sys.stderr)
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
payload = {
    "name": GITHUB_REPO,
    "description": "Automated NYC concert discovery and database agent",
    "private": True,
    "auto_init": False,
}

print(f"Creating repo {GITHUB_ORG}/{GITHUB_REPO} on GitHub…")
response = httpx.post(
    f"https://api.github.com/orgs/{GITHUB_ORG}/repos",
    headers=headers,
    json=payload,
)

if response.status_code == 201:
    clone_url = response.json()["clone_url"]
    ssh_url = response.json()["ssh_url"]
    print(f"Repo created: {response.json()['html_url']}")
    print(f"Clone URL: {clone_url}")
elif response.status_code == 422:
    print(f"Repo may already exist (422). Proceeding with push.")
    clone_url = f"https://github.com/{GITHUB_ORG}/{GITHUB_REPO}.git"
    ssh_url = f"git@github.com:{GITHUB_ORG}/{GITHUB_REPO}.git"
else:
    print(f"GitHub API error {response.status_code}: {response.text}", file=sys.stderr)
    sys.exit(1)

# Set remote and push
remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_ORG}/{GITHUB_REPO}.git"
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    if result.stdout:
        print(result.stdout.strip())

# Check if remote origin already exists
check = subprocess.run(
    ["git", "remote", "get-url", "origin"],
    cwd=project_root,
    capture_output=True,
    text=True,
)
if check.returncode == 0:
    run(["git", "remote", "set-url", "origin", remote_url])
else:
    run(["git", "remote", "add", "origin", remote_url])

run(["git", "push", "-u", "origin", "main"])
print(f"Pushed to https://github.com/{GITHUB_ORG}/{GITHUB_REPO}")
