import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()
key = os.getenv("GROQ_API_KEY")
print("Groq key found:", bool(key), "| length:", len(key) if key else 0)
print("Starts with gsk_:", key.startswith("gsk_") if key else False)

client = Groq(api_key=key, timeout=15.0)

print("\n--- Models this key can use ---")
try:
    listing = client.models.list()
    ids = [m.id for m in listing.data]
    for model_id in ids:
        print(model_id)
except Exception as e:
    print("Could not list models:", e)
    ids = []

skip_words = ["whisper", "tts", "guard", "orpheus", "playai", "compound", "embed"]
candidates = [i for i in ids if not any(w in i.lower() for w in skip_words)]
if not candidates:
    candidates = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]

print("\n--- Test questions ---")
for model_id in candidates[:6]:
    print("\nTrying", model_id)
    try:
        r = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "Say hello in five words."}],
            max_tokens=100,
        )
        print("WORKED:", r.choices[0].message.content)
    except Exception as e:
        print("FAILED:", e)

print("\nDone.")