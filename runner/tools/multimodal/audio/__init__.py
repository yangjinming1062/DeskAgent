"""Local STT / TTS for Runner.

The runner ships with two local engines for the voice stack that
Desktop's companion layer drives:

* Speech-to-text — ``faster-whisper`` (CTranslate2 + Silero VAD),
  matched to the hermes-agent default for offline transcription.
* Text-to-speech — ``piper-tts`` (VITS, 44 languages, fast on CPU),
  falling back to ``pyttsx3`` if Piper is unavailable. Both run
  offline; no cloud credentials are touched (Runner zero-cred
  invariant from [ARCHITECTURE §11]).

Capabilities are advertised at handshake time so the Desktop can hide
voice-call UI when no local engine is available — see
``utils.capabilities.snapshot``.
"""

from . import audio_io  # noqa: F401 — shared audio normalization helpers
from . import stt_tool  # noqa: F401 — registers ``speech_to_text``
from . import tts_tool  # noqa: F401 — registers ``text_to_speech``
