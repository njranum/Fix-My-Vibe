import os, time, json
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
from src.foundry_utils import get_client

client = get_client()

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Returns the current time",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]

agent = client.agents.create_agent(
    model="Phi-4-reasoning",
    name="tool-test",
    instructions="When asked for the time, call get_time() and return the result.",
    tools=tools,
)
print(f"Agent created: {agent.id}")

thread = client.agents.threads.create()
client.agents.messages.create(thread_id=thread.id, role="user", content="What time is it?")
run = client.agents.runs.create(thread_id=thread.id, agent_id=agent.id)
print(f"Run initial status: {run.status!r}")

for i in range(30):
    time.sleep(1)
    run = client.agents.runs.get(thread_id=thread.id, run_id=run.id)
    status_str = str(run.status)
    print(f"  [{i+1}] {status_str}")
    if status_str not in ("<RunStatus.QUEUED: 'queued'>", "<RunStatus.IN_PROGRESS: 'in_progress'>",
                          "queued", "in_progress"):
        break

if hasattr(run, "last_error") and run.last_error:
    print(f"last_error: {run.last_error}")

print(f"Final status: {run.status!r}")
client.agents.delete_agent(agent.id)
