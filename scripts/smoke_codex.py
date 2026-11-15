"""Small opt-in live check, using the user's existing Codex account."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai import Agent, WorkflowError, codex_status, obj
from app import load_env


def main():
    load_env()
    status = codex_status()
    print(status["message"], flush=True)
    if not status["configured"]:
        return 1
    agent = Agent()
    try:
        result = agent.call("Return ready=true as a connectivity check. No research is needed.",
                            {}, obj(ready={"type": "boolean"}))
    except WorkflowError:
        if hasattr(agent, "last_error"):
            # Only this synthetic connectivity check emits diagnostics; no real CV is used.
            print(json.dumps(agent.last_error, ensure_ascii=False), file=sys.stderr)
        raise
    if not result["ready"]:
        raise WorkflowError("Codex did not pass the connectivity check.")
    print("Codex structured output: passed.", flush=True)
    research = agent.call("Use live web search to find the official Codex CLI documentation. Report its title and source URL in one sentence. Use only official OpenAI documentation.",
                          {}, search=True)
    if not research["sources"]:
        raise WorkflowError("Live search returned no sources.")
    print("Codex live search and source output: passed.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except WorkflowError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
