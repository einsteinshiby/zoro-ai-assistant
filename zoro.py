import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import winsound

import pyttsx3
import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq

try:
    import pythoncom  # lets Zoro speak from a background thread
except ImportError:
    pythoncom = None

load_dotenv()  # reads your key from the .env file


# ---------- SETTINGS YOU CAN CHANGE ----------
VOICE_HINT = "zira"          # part of a voice name, e.g. "zira", "david"
VOICE_RATE = 175             # speaking speed (150 = slow, 200 = fast)
AWAKE_SECONDS = 30           # how long Zoro stays awake after hearing his name
SPEECH_LANGUAGE = "en-IN"    # "en-IN" Indian English, "en-US" American, "en-GB" British
PAUSE_SECONDS = 1.2          # how long a pause ends your sentence
SHORT_SENTENCES = 2          # normal answers are at most this many sentences
SPEAK_TYPED_REPLIES = True   # True = Zoro also speaks the answer to typed commands
SHOW_LISTENING = False       # True = print "Listening..." (it can interrupt your typing)
SHOW_STARTUP_INFO = False    # True = print the voices and AI models at startup
DEBUG_SLEEP = False          # True = show what Zoro hears while he is asleep
REASONING_EFFORT = "medium"  # "low" = faster, "medium" = more careful, "high" = slowest
TEMPERATURE = 0.3            # lower = less random and less likely to invent things
WAKE_PATTERN = re.compile(r"\b(zoro|zorro|zero|joro)\b")   # names Zoro answers to

recognizer = sr.Recognizer()
recognizer.pause_threshold = PAUSE_SECONDS

speak_lock = threading.Lock()
command_queue = queue.Queue()      # items: ("voice", text) or ("typed", text)
busy = threading.Event()           # set while Zoro is working on a command
stop_event = threading.Event()     # set when Zoro is shutting down
state = {"awake_until": 0, "speak_count": 0, "mute": False, "last_question": ""}


# ---------- VOICE OUTPUT ----------
def pick_voice(engine):
    for v in engine.getProperty("voices"):
        if VOICE_HINT.lower() in v.name.lower():
            engine.setProperty("voice", v.id)
            return


def speak(text):
    print("Zoro:", text, flush=True)
    if state["mute"]:
        return                       # typed command with spoken replies turned off
    with speak_lock:
        state["speak_count"] += 1    # lets the microphone ignore Zoro's own voice
        if pythoncom:
            pythoncom.CoInitialize()
        engine = pyttsx3.init()
        pick_voice(engine)
        engine.setProperty("rate", VOICE_RATE)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
        state["speak_count"] += 1


def show_voices():
    try:
        engine = pyttsx3.init()
        names = [v.name for v in engine.getProperty("voices")]
        engine.stop()
        print("Voices on this PC:", " | ".join(names))
    except Exception:
        pass


# ---------- VOICE INPUT ----------
def listen(quiet=False):
    """quiet=True prints nothing."""
    with sr.Microphone() as source:
        if quiet:
            recognizer.adjust_for_ambient_noise(source, duration=0.2)
        else:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            print("Listening...", flush=True)
        try:
            audio = recognizer.listen(source, timeout=8, phrase_time_limit=12)
        except sr.WaitTimeoutError:
            return ""
    try:
        return recognizer.recognize_google(audio, language=SPEECH_LANGUAGE).lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        if not quiet:
            print("Zoro: No internet or the speech service is down.")
        time.sleep(2)
        return ""


def voice_thread():
    """Keeps listening to the microphone and puts what it hears in the queue."""
    while not stop_event.is_set():
        if busy.is_set() or speak_lock.locked():
            time.sleep(0.1)
            continue
        before = state["speak_count"]
        asleep = time.time() >= state["awake_until"]
        try:
            text = listen(quiet=(asleep or not SHOW_LISTENING))
        except Exception as e:
            print("Microphone problem:", str(e)[:100])
            time.sleep(3)
            continue
        # throw the result away if Zoro spoke or was busy while we listened
        if not text or busy.is_set() or state["speak_count"] != before:
            continue
        command_queue.put(("voice", text))


def typing_thread():
    """Reads what you type in the terminal and puts it in the queue."""
    while not stop_event.is_set():
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            return
        line = line.strip()
        if line:
            command_queue.put(("typed", line.lower()))


# ---------- AI BRAIN (Groq, free) ----------
BASE_PROMPT = (
    "You are Zoro, a friendly voice assistant built by Einstein. "
    "The user's name is Einstein. "
    "Your answers are spoken aloud, so use plain text only: no markdown, "
    "lists, bullet points, headings, or emojis. "
    "Accuracy matters more than sounding confident. "
    "If a name, person, or topic in the question is unclear or could mean "
    "more than one thing, ask one short question to clarify instead of guessing. "
    "If you are not sure of a fact, say you are not sure and suggest that the "
    "user says 'google that' to check it. "
    "You do NOT have internet access or live data. If asked about current "
    "scores, match results, news, weather, prices, or anything recent "
    "that you cannot know for sure, say you cannot check that. "
    "Never guess or make up facts, names, numbers, or results. "
)

SHORT_RULE = (
    f"IMPORTANT: Reply in at most {SHORT_SENTENCES} short sentences. "
    "Be direct, give only the answer, and skip extra background."
)

LONG_RULE = (
    "The user asked for a longer answer. Write it in plain paragraphs "
    "(no lists or markdown) and follow the length they requested."
)

# phrases that mean "give me a long answer"
LONG_PHRASES = ["long paragraph", "long answer", "long explanation", "in detail", "detailed",
                "elaborate", "in depth", "in-depth", "essay", "paragraph", "write a story",
                "tell me a story", "full explanation", "explain fully", "tell me more",
                "more details", "more detail", "step by step"]

# Zoro asks Groq which models your key can use, then tries them in this order.
# Bigger models first = more accurate. Smaller ones are used if the bigger ones are busy.
PREFERRED_GROQ = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "llama-3.3-70b-versatile",
    "allam-2-7b",
    "llama-3.1-8b-instant",
]
NOT_CHAT_WORDS = ["whisper", "tts", "guard", "orpheus", "playai",
                  "compound", "embed", "safeguard"]

groq_client = None
if os.getenv("GROQ_API_KEY"):
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=40.0)


def discover_groq_models():
    """Asks Groq which models this key can really use."""
    try:
        listing = groq_client.models.list()
        ids = [m.id for m in listing.data if getattr(m, "active", True)]
    except Exception as e:
        print("(could not get the Groq model list:", str(e)[:120], ")")
        return list(PREFERRED_GROQ)
    chat = [i for i in ids if not any(w in i.lower() for w in NOT_CHAT_WORDS)]
    if SHOW_STARTUP_INFO:
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


def call_groq(name, messages, max_tokens):
    extra = {}
    if "gpt-oss" in name:
        extra["reasoning_effort"] = REASONING_EFFORT
    try:
        return groq_client.chat.completions.create(
            model=name, messages=messages, max_tokens=max_tokens,
            temperature=TEMPERATURE, **extra
        )
    except Exception as e:
        if "400" in str(e) or isinstance(e, TypeError):
            # this model did not like one of the extra settings: ask plainly
            return groq_client.chat.completions.create(
                model=name, messages=messages, max_tokens=max_tokens
            )
        raise


def clean_answer(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return text.strip()


def parse_word_target(text):
    """Finds requests like '150 to 200 words' or 'in 100 words'. Returns (low, high) or None."""
    m = re.search(r"(\d+)\s*(?:-|–|to|and|or)\s*(\d+)\s*words", text)
    if m:
        low, high = sorted((int(m.group(1)), int(m.group(2))))
    else:
        m = re.search(r"(\d+)\s*words", text)
        if not m:
            return None
        n = int(m.group(1))
        low, high = int(n * 0.9), int(n * 1.1)
    if high < 10 or high > 800:
        return None
    return low, high


def wants_long_answer(text):
    return any(p in text for p in LONG_PHRASES)


def shorten(text, max_sentences):
    """Keeps only the first few sentences of a short answer."""
    if len(text.split()) <= 45:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(sentences) <= max_sentences:
        return text
    return " ".join(sentences[:max_sentences])


def run_models(messages, errors, max_tokens):
    """Tries the AI models one by one. Returns (answer, model name) or (None, None)."""
    for attempt in range(2):
        for name in GROQ_MODELS:
            if is_resting(name):
                continue
            try:
                r = call_groq(name, messages, max_tokens)
                text = clean_answer(r.choices[0].message.content)
                if text:
                    return text, name
                rest_model(name, "empty", seconds=300)   # model gave no text
                print(f"(skipped {name}: empty answer)")
            except Exception as e:
                msg = str(e)
                errors.append(msg.lower())
                print(f"(skipped {name}: {msg[:110]})")
                rest_model(name, msg)
        model_rest_until.clear()   # everything failed: forget the rest list, retry once
    return None, None


def ask_ai(question):
    if groq_client is None:
        speak("Sorry, I can't do that yet. My AI brain is not set up.")
        return

    state["last_question"] = question
    chat_history.append({"role": "user", "content": question})
    del chat_history[:-10]  # keep only the last 10 messages

    # decide: short answer (default) or long answer (only when the user asks)
    target = parse_word_target(question)
    long_mode = bool(target) or wants_long_answer(question)
    max_tokens = 800
    if target:
        low, high = target
        system = (BASE_PROMPT + LONG_RULE +
                  f" The answer must be between {low} and {high} words. "
                  f"Aim for about {(low + high) // 2} words.")
        max_tokens = min(4000, high * 3 + 800)
    elif long_mode:
        system = BASE_PROMPT + LONG_RULE
        max_tokens = 2000
    else:
        system = BASE_PROMPT + SHORT_RULE
    messages = [{"role": "system", "content": system}] + chat_history

    if not state["mute"]:
        winsound.Beep(800, 120)  # short beep: "I heard you, thinking"
    print("Thinking...", flush=True)
    started = time.time()
    errors = []
    answer, used = run_models(messages, errors, max_tokens)

    # Python counts the words itself, because AI models are bad at counting
    if answer and target:
        low, high = target
        for _ in range(2):
            n = len(answer.split())
            if low <= n <= high:
                break
            print(f"(answer had {n} words, asking for {low}-{high})", flush=True)
            fix = (f"Your answer had {n} words. Rewrite it so it has between "
                   f"{low} and {high} words. Reply with only the rewritten answer.")
            retry = messages + [{"role": "assistant", "content": answer},
                                {"role": "user", "content": fix}]
            better, used2 = run_models(retry, errors, max_tokens)
            if not better:
                break
            answer, used = better, used2

    # short mode: make sure the answer really is short
    if answer and not long_mode:
        answer = shorten(answer, SHORT_SENTENCES)

    if not answer:
        chat_history.pop()  # drop the question that failed
        all_errors = " ".join(errors)
        if "429" in all_errors or "rate limit" in all_errors:
            speak("I have reached my free question limit for now. Try again in a minute.")
        else:
            speak("The AI service is not responding right now. Please ask me again in a minute.")
        return

    print(f"(answered by {used} in {time.time() - started:.1f} seconds, "
          f"{len(answer.split())} words)")
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
STOP_WORDS = ["stop", "exit", "quit", "goodbye", "good bye", "bye", "shut down", "shutdown"]
SLEEP_WORDS = ["go to sleep", "sleep now", "be quiet", "that's all", "thats all", "that is all"]

TIME_PHRASES = ["what time is it", "what is the time", "what's the time", "whats the time",
                "what is time", "tell me the time", "current time", "time now",
                "time is it", "the time please", "what time it is"]

GOOGLE_THAT_PHRASES = ["google that", "google it", "search that", "search it",
                       "check on google", "check that on google", "verify that",
                       "look it up", "look that up", "search this on google"]

# topics the AI cannot know without internet, so Zoro searches Google instead
LIVE_TOPICS = ["score", "who won", "match result", "live ", "weather", "temperature",
               "news", "headlines", "stock price", "share price", "price of",
               "latest", "trending", "right now", "forecast"]


def is_stop(command):
    # only counts as a stop request if it is a short sentence,
    # so questions like "why do buses stop" go to the AI instead
    if len(command.split()) > 6:
        return False
    return any(w in command for w in STOP_WORDS)


def is_sleep(command):
    if len(command.split()) > 6:
        return False
    return any(w in command for w in SLEEP_WORDS)


def is_time_question(command):
    if len(command.split()) > 8 or "zone" in command:
        return False
    return any(p in command for p in TIME_PHRASES)


def is_date_question(command):
    if len(command.split()) > 8 or " of " in command:
        return False
    if re.search(r"\bdate\b", command) and re.search(r"\b(what|whats|what's|tell|current|today)\b", command):
        return True
    return "what day" in command or "what's today" in command or "what is today" in command


def wants_google_check(command):
    if len(command.split()) > 7:
        return False
    return any(p in command for p in GOOGLE_THAT_PHRASES)


def needs_live_info(command):
    if len(command.split()) > 14:
        return False
    padded = command + " "
    return any(t in padded for t in LIVE_TOPICS)


def google_search(query):
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))


def open_camera():
    subprocess.Popen("start microsoft.windows.camera:", shell=True)


def handle(command):
    """Returns True (keep going), False (quit Zoro) or "sleep" (wait for his name)."""

    if is_sleep(command):
        speak("Okay, going to sleep. Say Zoro when you need me.")
        return "sleep"

    if is_stop(command):
        speak("Goodbye Einstein")
        return False

    elif wants_google_check(command):
        if state["last_question"]:
            speak("Okay, searching that on Google so you can check it.")
            google_search(state["last_question"])
        else:
            speak("I don't have a question to check yet. Ask me something first.")

    elif "remind me" in command:
        add_reminder(command)

    elif "my reminders" in command or "list reminders" in command:
        list_reminders()

    elif "clear reminders" in command or "delete reminders" in command:
        clear_reminders()

    elif "camera" in command:
        speak("Opening camera")
        open_camera()

    elif is_time_question(command):
        now = datetime.datetime.now().strftime("%I:%M %p")
        speak(f"It is {now}")

    elif is_date_question(command):
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
        google_search(query)

    elif command in ("hello", "hi", "hey"):
        speak("Hello Einstein! How can I help you?")

    elif needs_live_info(command):
        state["last_question"] = command
        speak("I can't check live information, so I'll search it on Google for you.")
        google_search(command)

    else:
        ask_ai(command)   # anything else goes to the AI brain

    return True


# ---------- MAIN LOOP ----------
def main():
    load_reminders()
    if groq_client is None:
        print("Note: AI brain is OFF (no GROQ_API_KEY found in .env).")
    if SHOW_STARTUP_INFO:
        show_voices()

    threading.Thread(target=voice_thread, daemon=True).start()
    threading.Thread(target=typing_thread, daemon=True).start()

    print("Tip: say 'Zoro, ...' or just type a command here and press Enter.", flush=True)
    busy.set()
    speak("Zoro is online. Say my name when you need me.")
    busy.clear()

    running = True
    state["awake_until"] = 0   # Zoro starts asleep until he hears his name

    while running:
        try:
            source, heard = command_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        woke = WAKE_PATTERN.search(heard) is not None
        asleep = time.time() >= state["awake_until"]

        if source == "voice":
            if asleep and not woke:
                if DEBUG_SLEEP:
                    print(f"(asleep, heard: {heard})")
                continue                  # asleep and no name heard: stay silent
            print("You said:", heard, flush=True)

        if woke:
            heard = WAKE_PATTERN.sub("", heard).strip(" ,.!?")

        busy.set()                        # pause the microphone while Zoro works
        try:
            if not heard:                 # he only heard his name
                speak("Yes Einstein?")
                result = True
            else:
                state["mute"] = (source == "typed" and not SPEAK_TYPED_REPLIES)
                result = handle(heard)
        finally:
            state["mute"] = False
            time.sleep(0.4)               # let the room go quiet before listening again
            busy.clear()

        if result is False:
            running = False
        elif result == "sleep":
            state["awake_until"] = 0
        else:
            state["awake_until"] = time.time() + AWAKE_SECONDS   # stay awake for follow-ups


try:
    main()
except KeyboardInterrupt:
    pass
finally:
    stop_event.set()
    print("\nZoro stopped.", flush=True)
    sys.stdout.flush()
    os._exit(0)   # closes the background threads cleanly, even if one is waiting for typing