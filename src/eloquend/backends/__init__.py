"""Built-in synthesis backends."""

from .base import AudioFormat, SynthesisBackend
from .openai_speech import OpenAISpeechBackend
from .piper import PiperBackend
from .tone import ToneBackend

__all__ = [
    "AudioFormat",
    "OpenAISpeechBackend",
    "PiperBackend",
    "SynthesisBackend",
    "ToneBackend",
]
