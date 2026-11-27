"""Text to speech with `google-genai` through AIRA, written to a .wav file.

    AUTH=key python google_genai_speech.py   # or: bearer, service-account, token-as-key

The answer is one audio part: 16-bit mono PCM at the rate its MIME type names
(`audio/L16;codec=pcm;rate=24000`), which a WAV header makes playable.
"""

import os
import sys
import wave

import httpx
from google import genai
from google.genai import types

GATEWAY_URL = os.environ["GATEWAY_URL"]
USE_CASE = os.environ["USE_CASE"]
SPEECH_MODEL = os.environ.get("SPEECH_MODEL", "gemini-2.5-flash-preview-tts")
OUT = os.environ.get("OUT", "speech.wav")
AUTH = os.environ.get("AUTH", "key")


def service_account_token() -> str:
    """Keycloak's client-credentials grant: a confidential client signs in as itself."""
    form = {"grant_type": "client_credentials"}
    form |= {"client_id": os.environ["CLIENT_ID"], "client_secret": os.environ["CLIENT_SECRET"]}
    response = httpx.post(os.environ["TOKEN_URL"], data=form)
    response.raise_for_status()
    return response.json()["access_token"]


headers: dict[str, str] = {}
if AUTH == "key":  # an AIRA API key, issued in the console: use case -> API keys
    api_key = os.environ["API_KEY"]
elif AUTH == "bearer":  # a person's Keycloak access token, as Authorization: Bearer
    api_key, headers["Authorization"] = "unused", f"Bearer {os.environ['ACCESS_TOKEN']}"
elif AUTH == "service-account":  # a service account's token, as Authorization: Bearer
    api_key, headers["Authorization"] = "unused", f"Bearer {service_account_token()}"
elif AUTH == "token-as-key":  # a token in the key's place, for clients without a header setting
    api_key = os.environ.get("ACCESS_TOKEN") or service_account_token()
else:
    sys.exit(f"unknown AUTH {AUTH!r}")

client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(
        base_url=f"{GATEWAY_URL}/uc/{USE_CASE}", api_version="v1beta", headers=headers
    ),
)

response = client.models.generate_content(
    model=SPEECH_MODEL,
    contents="Say cheerfully: Good morning, and welcome!",
    config=types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
            )
        ),
    ),
)
audio = response.candidates[0].content.parts[0].inline_data
if audio is None or not audio.data:
    sys.exit("the answer carried no audio")
rate = int((audio.mime_type or "").partition("rate=")[2].split(";")[0] or 24000)

with wave.open(OUT, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(rate)
    wav.writeframes(audio.data)
print(f"{OUT}: {len(audio.data) / 2 / rate:.1f} s of audio at {rate} Hz")
