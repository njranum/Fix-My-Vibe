import os, json
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
from src.foundry_utils import get_client

client = get_client()
chat = client.inference.get_chat_completions_client()
print(f"Chat client: {type(chat)}")

response = chat.complete(
    model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
    messages=[
        {"role": "system", "content": "You are a JSON API. Return ONLY valid JSON, no other text."},
        {"role": "user", "content": 'Return {"status": "ok", "value": 42}'},
    ],
)
print(f"Response: {response.choices[0].message.content}")
