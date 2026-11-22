import datetime
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
import winsound

import pyttsx3
import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq

try:
    import pythoncom  # lets Zoro speak from a background timer
except ImportError:
    pythoncom = None

load_dotenv()  # reads your key from the .env file

recognizer = sr.Recognizer()
speak_lock = threading.Lock()


# ---------- VOICE OUTPUT ----------
def speak(text):
    print("Zoro:", text)
    with speak_lock:
        if pythoncom:
            pythoncom.CoInitialize()
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        engine.stop()


# ---------- VOICE INPUT ----------
def listen():
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("Listening...")
        try:
            audio = recognizer.listen(source, timeout=8, phrase_time_limit=10)
        except sr.WaitTimeoutError:
            return ""
    try:
        text = recognizer.recognize_google(audio)
        print("You said:", text)
        return text.lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        print("Zoro: No internet or the speech service is down.")
        return ""


# ---------- AI BRAIN (Groq, free) ----------
SYSTEM_PROMPT = (
    "You are Zoro, a friendly voice assistant built by Einstein. "
    "The user's name is Einstein. "
    "Your answers are spoken aloud, so reply in one to three short, "
    "plain sentences. Never use markdown, lists, bullet points, or emojis."
)

# Zoro asks Groq which models your key can use, then tries them in this order.
# Models not in your list are ignored automatically.
PREFERRED_GROQ = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
]
NOT_CHAT_WORDS = ["whisper", "tts", "guard", "orpheus", "playai",
                  "compound", "embed", "safeguard"]

groq_client = None
if os.getenv("GROQ_API_KEY"):
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=20.0)


def discover_groq_models():
    """Asks Groq which models this key can really use."""
    try:
        listing = groq_client.models.list()
        ids = [m.id for m in listing.data if getattr(m, "active", True)]
    except Exception as e:
        print("(could not get the Groq model list:", str(e)[:120], ")")
        return list(PREFERRED_GROQ)
    chat = [i for i in ids if not any(w in i.lower() for w in NOT_CHAT_WORDS)]
    print("Groq models available to you:", ", ".join(chat) if chat else "NONE")
    ordered = [m for m in PREFERRED_GROQ if m in chat]
    ordered += [m for m in chat if m not in ordered]
    return ordered


GROQ_MODELS = discover_groq_models() if groq_client else []

chat_history = []  # recent conversation: {"role": "user"/"assistant", "content": "..."}
model_rest_until = {}  # model name -> time when Zoro may try it again


def is_resting(name):
    return model_rest_until.get(name, 0) > time.time()


def rest_model(name, msg, seconds=None):
    low = msg.lower()
    gone = "404" in msg or "not_found" in low or "decommissioned" in low
    if seconds is None:
        seconds = 3600 if gone else 90
    model_rest_until[name] = time.time() + seconds


def call_groq(name, messages):
    extra = {}
    if "gpt-oss" in name:
        extra["reasoning_effort"] = "low"   # think less = faster answers
    try:
        return groq_client.chat.completions.create(
            model=name, messages=messages, max_tokens=800, **extra
        )
    except Exception as e:
        if extra and ("400" in str(e) or isinstance(e, TypeError)):
            return groq_client.chat.completions.create(
                model=name, messages=messages, max_tokens=800
            )
        raise


def clean_answer(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return text.strip()


def ask_ai(question):
    if groq_client is None:
        speak("Sorry, I can't do that yet. My AI brain is not set up.")
        return

    chat_history.append({"role": "user", "content": question})
    del chat_history[:-10]  # keep only the last 10 messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_history

    winsound.Beep(800, 120)  # short beep: "I heard you, thinking"
    print("Thinking...")
    started = time.time()
    errors = []
    answer, used = None, None

    for attempt in range(2):
        for name in GROQ_MODELS:
            if is_resting(name):
                continue
            try:
                r = call_groq(name, messages)
                text = clean_answer(r.choices[0].message.content)
                if text:
                    answer, used = text, name
                    break
                rest_model(name, "empty", seconds=300)   # model gave no text
                print(f"(skipped {name}: empty answer)")
            except Exception as e:
                msg = str(e)
                errors.append(msg.lower())
                print(f"(skipped {name}: {msg[:110]})")
                rest_model(name, msg)
        if answer:
            break
        model_rest_until.clear()   # everything failed: forget the rest list, retry once

    if not answer:
        chat_history.pop()  # drop the question that failed
        all_errors = " ".join(errors)
        if "429" in all_errors or "rate limit" in all_errors:
            speak("I have reached my free question limit for now. Try again in a minute.")
        else:
            speak("The AI service is not responding right now. Please ask me again in a minute.")
        return

    print(f"(answered by {used} in {time.time() - started:.1f} seconds)")
    chat_history.append({"role": "assistant", "content": answer})
    speak(answer)


# ---------- REMINDERS ----------
REMINDER_FILE = "reminders.json"
reminders = []  # each one: {"text": "...", "due": "2026-11-21T22:30:00"}

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60,
}


def save_reminders():
    with open(REMINDER_FILE, "w") as f:
        json.dump(reminders, f, indent=2)


def load_reminders():
    if not os.path.exists(REMINDER_FILE):
        return
    try:
        with open(REMINDER_FILE) as f:
            saved = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    for r in saved:
        reminders.append(r)
        schedule_reminder(r)


def fire_reminder(r):
    if r in reminders:
        reminders.remove(r)
        save_reminders()
    winsound.Beep(1000, 400)
    speak(f"Reminder: {r['text']}")


def schedule_reminder(r):
    due = datetime.datetime.fromisoformat(r["due"])
    delay = max((due - datetime.datetime.now()).total_seconds(), 1)
    timer = threading.Timer(delay, fire_reminder, args=[r])
    timer.daemon = True
    timer.start()


def parse_reminder(command):
    """Returns (text, seconds) or None."""
    unit_pattern = r"(second|seconds|minute|minutes|hour|hours)"
    m = re.search(rf"remind me to (.+?) in (\w+) {unit_pattern}", command)
    if m:
        task, amount, unit = m.group(1), m.group(2), m.group(3)
    else:
        m = re.search(rf"remind me in (\w+) {unit_pattern} to (.+)", command)
        if not m:
            return None
        amount, unit, task = m.group(1), m.group(2), m.group(3)

    if amount.isdigit():
        number = int(amount)
    else:
        number = NUMBER_WORDS.get(amount)
    if number is None:
        return None

    if unit.startswith("hour"):
        seconds = number * 3600
    elif unit.startswith("minute"):
        seconds = number * 60
    else:
        seconds = number
    return task.strip(), seconds


def add_reminder(command):
    parsed = parse_reminder(command)
    if not parsed:
        speak("Say it like this: remind me to drink water in 10 minutes.")
        return
    task, seconds = parsed
    due = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    r = {"text": task, "due": due.isoformat(timespec="seconds")}
    reminders.append(r)
    save_reminders()
    schedule_reminder(r)
    speak(f"Okay, I will remind you to {task} at {due.strftime('%I:%M %p')}")


def list_reminders():
    if not reminders:
        speak("You have no reminders.")
        return
    speak(f"You have {len(reminders)} reminders.")
    for r in reminders:
        due = datetime.datetime.fromisoformat(r["due"]).strftime("%I:%M %p")
        speak(f"{r['text']} at {due}")


def clear_reminders():
    reminders.clear()
    save_reminders()
    speak("All reminders cleared. Ones already running may still ring once.")


# ---------- COMMANDS ----------
STOP_WORDS = ["stop", "exit", "quit", "goodbye", "good bye", "bye",
              "shut down", "shutdown", "go to sleep"]


def is_stop(command):
    # only counts as a stop request if it is a short sentence,
    # so questions like "why do buses stop" go to the AI instead
    if len(command.split()) > 6:
        return False
    return any(w in command for w in STOP_WORDS)


def open_camera():
    subprocess.Popen("start microsoft.windows.camera:", shell=True)


def handle(command):
    """Returns False when Zoro should shut down."""

    if is_stop(command):
        speak("Goodbye Einstein")
        return False

    elif "remind me" in command:
        add_reminder(command)

    elif "my reminders" in command or "list reminders" in command:
        list_reminders()

    elif "clear reminders" in command or "delete reminders" in command:
        clear_reminders()

    elif "camera" in command:
        speak("Opening camera")
        open_camera()

    elif "what time" in command or "current time" in command:
        now = datetime.datetime.now().strftime("%I:%M %p")
        speak(f"It is {now}")

    elif "what date" in command or "today's date" in command or "what day" in command:
        today = datetime.datetime.now().strftime("%A, %d %B %Y")
        speak(f"Today is {today}")

    elif "youtube" in command:
        speak("Opening YouTube")
        webbrowser.open("https://www.youtube.com")

    elif "notepad" in command:
        speak("Opening Notepad")
        os.system("start notepad")

    elif "calculator" in command:
        speak("Opening calculator")
        os.system("start calc")

    elif command.startswith("search "):
        query = command.split("search", 1)[1].strip()
        speak(f"Searching for {query}")
        webbrowser.open(f"https://www.google.com/search?q={query}")

    elif command in ("hello", "hi", "hey"):
        speak("Hello Einstein! How can I help you?")

    else:
        ask_ai(command)   # anything else goes to the AI brain

    return True


# ---------- MAIN LOOP ----------
def main():
    load_reminders()
    if groq_client is None:
        print("Note: AI brain is OFF (no GROQ_API_KEY found in .env).")
    speak("Zoro is online. How can I help?")
    running = True
    while running:
        heard = listen()
        if not heard:
            continue
        heard = heard.replace("zoro", "").strip()
        if heard:
            running = handle(heard)


try:
    main()
except KeyboardInterrupt:
    print("\nZoro stopped.")