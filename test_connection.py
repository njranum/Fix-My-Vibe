"""
test_connection.py
Smoke test — confirms Azure AI Foundry and Tavily connections work.
Run: python test_connection.py
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


def check_env() -> bool:
    required = ["FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT_NAME", "TAVILY_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("ERROR: missing required env vars:")
        for k in missing:
            print(f"  - {k}")
        print("\nCopy .env.example to .env and fill in your values.")
        return False
    return True


def test_tavily() -> bool:
    print("\n[ 1/2 ] Testing Tavily...")
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        result = client.search(query="Claude Code CLAUDE.md best practices", max_results=2)
        hits = len(result.get("results", []))
        print(f"  Tavily OK — {hits} results returned")
        return True
    except ImportError:
        print("  ERROR: tavily-python not installed. Run: pip install tavily-python")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_foundry() -> bool:
    print("\n[ 2/2 ] Testing Azure AI Foundry...")
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        print("  ERROR: azure-ai-projects not installed. Run: pip install azure-ai-projects azure-identity")
        return False

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]

    print(f"  Endpoint: {endpoint}")
    print(f"  Model:    {model}")

    try:
        client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    except Exception as e:
        print(f"  ERROR connecting: {e}")
        print("  Tip: run 'az login' if DefaultAzureCredential fails")
        return False

    print("  Connected. Creating smoke-test agent...")

    try:
        agent = client.agents.create_agent(
            model=model,
            name="fix-my-vibe-smoke-test",
            instructions="You are a test agent. Reply with exactly the word: PASS",
        )
    except Exception as e:
        print(f"  ERROR creating agent: {e}")
        print("  Tip: check deployment name matches exactly in Foundry portal → Models + Endpoints")
        return False

    try:
        thread = client.agents.threads.create()
        client.agents.messages.create(thread_id=thread.id, role="user", content="Reply PASS")

        run = client.agents.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)

        if run.status != "completed":
            print(f"  ERROR: run ended with status '{run.status}'")
            return False

        response = ""
        for msg in client.agents.messages.list(thread_id=thread.id):
            if msg.role == "assistant":
                raw = msg.content[0].text.value
                # Strip <think> blocks emitted by Phi-4-reasoning
                response = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                break

        print(f"  Agent response: {response!r}")
        print(f"  Foundry OK — run completed, model responded")
        return True

    except Exception as e:
        print(f"  ERROR during run: {e}")
        return False

    finally:
        try:
            client.agents.delete_agent(agent.id)
        except Exception:
            pass


def main():
    print("Fix My Vibe — connection smoke test\n")

    if not check_env():
        sys.exit(1)

    tavily_ok = test_tavily()
    foundry_ok = test_foundry()

    print("\n" + "=" * 50)
    print(f"  Tavily:  {'PASS' if tavily_ok else 'FAIL'}")
    print(f"  Foundry: {'PASS' if foundry_ok else 'FAIL'}")
    print("=" * 50)

    if tavily_ok and foundry_ok:
        print("\n  All checks passed. Ready to build.\n")
    else:
        print("\n  Fix the failing checks above before Day 1.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
