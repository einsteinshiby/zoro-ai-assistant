# Zoro: Personal Voice AI Assistant

Zoro is a voice-controlled personal assistant for Windows, built in Python. You speak to it, it understands, performs tasks on your computer, and answers general questions out loud using a free AI model from Groq.

## Features

- **Voice input and output:** speech recognition plus text-to-speech
- **Open apps:** camera, Notepad, Calculator, YouTube
- **Web search:** "search python tutorials"
- **Reminders:** "remind me to drink water in 10 minutes" (saved to a file, so they survive restarts)
- **Time and date:** "what time is it", "what's the date"
- **Ask anything:** general questions answered by an AI model, with short-term conversation memory
- **Automatic model selection:** Zoro asks Groq which models your key can use and falls back to others if one fails

## Requirements

- Windows 10 or 11
- Python 3.12
- A working microphone and speakers
- A free Groq API key from https://console.groq.com/keys

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/einsteinshiby/zoro-ai-assistant.git
   cd zoro-ai-assistant
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv zoro-env
   zoro-env\Scripts\activate
   ```

3. Install the libraries:
   ```
   pip install -r requirements.txt
   ```
   If `pyaudio` fails to install, run `pip install pipwin` and then `pipwin install pyaudio`.

4. Create a file named `.env` in the project folder (copy `.env.example`) and add your key:
   ```
   GROQ_API_KEY=your-groq-key-here
   ```

5. Run Zoro:
   ```
   python zoro.py
   ```

## Example commands

| Say | Zoro does |
|---|---|
| "Open camera" | Opens the Windows Camera app |
| "What time is it" | Tells you the time |
| "Open YouTube" | Opens YouTube in your browser |
| "Search healthy breakfast ideas" | Runs a Google search |
| "Remind me to stretch in 10 minutes" | Sets a spoken reminder |
| "Who invented the telephone" | Answers using the AI model |
| "You can stop now" | Shuts Zoro down |

Tip: speak a full sentence in one go. Short single words are recognized less reliably.

## How it works

1. `speech_recognition` captures audio from the microphone and converts it to text.
2. Commands like opening apps and setting reminders are handled directly in Python.
3. Anything else is sent to a Groq-hosted language model, and the reply is spoken with `pyttsx3`.

## Project status and ideas

- [x] Voice input and output
- [x] App launching and web search
- [x] Reminders
- [x] AI question answering
- [x] Wake word ("Zoro")
- [ ] More natural voice
- [ ] Desktop window with buttons
- [ ] Music and weather commands

## Security

Never commit your `.env` file. It is listed in `.gitignore` so your API key stays on your computer.

## Author

Einstein Shiby
