import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
print("Key found:", bool(key), "| key length:", len(key) if key else 0)

# gives up on any request after 15 seconds instead of hanging
client = genai.Client(
    api_key=key,
    http_options=types.HttpOptions(timeout=15000),
)

models = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

for model_name in models:
    print("\nTrying", model_name)
    start = time.time()
    try:
        r = client.models.generate_content(
            model=model_name,
            contents="Say hello in five words.",
        )
        print("WORKED in", round(time.time() - start, 1), "seconds:", r.text)
    except Exception as e:
        print("FAILED after", round(time.time() - start, 1), "seconds:", str(e)[:150])

print("\nDone.")