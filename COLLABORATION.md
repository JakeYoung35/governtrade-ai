# Working together on GovernTrade AI

Shared repository: https://github.com/JakeYoung35/governtrade-ai

Each collaborator has a separate copy of the source, a separate Codex chat, and separate private account connections and demo data. Source changes become shared after committing, pushing and merging; this is not simultaneous editing of a single website.

## First-time setup for a collaborator

1. Accept the repository invitation from GitHub.
2. Clone the repository through GitHub Desktop, or run `git clone https://github.com/JakeYoung35/governtrade-ai.git`.
3. Add the cloned folder as a local project in your own Codex app.
4. Ask Codex: “Read README.md and COLLABORATION.md. This is an existing application: preserve the GovernPay-style design and independent trade governance. Start the local preview and wait for my requested changes.”
5. Install Python 3.12 or later if it is missing. Install requirements.txt in a Python virtual environment; see README for secure vault setup.
6. Run `python3 server.py` from the cloned folder. Windows may use `py -3 server.py`. Open http://localhost:3110.

## Before a new change

Finish and commit or safely stash current edits. Switch to main and pull the latest shared version. Create a fresh descriptive branch from main. Coordinate with your teammate so both assistants do not change the same sections unnecessarily.

Example assistant prompt:

“Check the working tree before modifying it. Pull the latest main only if there are no uncommitted changes. Create a new branch named maddox-agent-cards from the updated main. Improve the agent selection cards while preserving the existing styling and governance engine. Run relevant checks and show me the result.”

## Share a finished change

Review the assistant’s diff and preview. Commit changes, push your branch, and open a pull request targeting main. Have your teammate review it before merging. GitHub Desktop offers these operations without command-line Git.

If both people changed overlapping code, ask the assistant to resolve the merge conflict, explain the combined behavior, and rerun relevant checks. Do not force-push shared main or discard your teammate’s changes.

After merging, each collaborator switches to main and pulls. Refresh the preview for frontend edits; stop and restart server.py for Python edits.

## Checks

- `python3 -m unittest discover -s . -p 'test_*.py' -v` (Postgres store tests run only when `TEST_DATABASE_URL` is set; they use and drop a temporary schema)
- `node --check static/app.js` if Node.js is installed.

Keep `.data/`, secrets and Python caches out of commits. The included .gitignore excludes the private encrypted credential store and account state and caches. Your local balance and trade records do not sync through GitHub.
