import os, time
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
from src.foundry_utils import get_client

client = get_client()

agent = client.agents.create_agent(
    model="Phi-4-reasoning",
    name="smoke-test",
    instructions="Reply with the word OK and nothing else.",
)
print(f"Agent created: {agent.id}")

thread = client.agents.threads.create()
client.agents.messages.create(thread_id=thread.id, role="user", content="Say OK")
run = client.agents.runs.create(thread_id=thread.id, agent_id=agent.id)
print(f"Run created: {run.id}, initial status: {run.status!r}")

for _ in range(20):
    time.sleep(1)
    run = client.agents.runs.get(thread_id=thread.id, run_id=run.id)
    print(f"  status: {run.status!r}")
    if str(run.status) not in ("RunStatus.QUEUED", "RunStatus.IN_PROGRESS", "queued", "in_progress"):
        break

if hasattr(run, "last_error") and run.last_error:
    print(f"Error: {run.last_error}")

for msg in client.agents.messages.list(thread_id=thread.id):
    if msg.role == "assistant":
        print(f"Response: {msg.content[0].text.value[:200]}")

client.agents.delete_agent(agent.id)
print("Done")
