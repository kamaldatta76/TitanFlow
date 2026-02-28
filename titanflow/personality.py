"""TitanFlow Personality Store — hot-reloadable in-memory config.

Stores live personality settings per TitanFlow instance name.
Updated via POST /api/personality from TitanPortal without requiring restart.

bot.py reads from this store on every message to build the system prompt modifier.
"""

from __future__ import annotations

import threading
from typing import Any

# Thread-safe global personality store keyed by instance name
_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}

# Default personality config
_DEFAULTS: dict[str, Any] = {
    "slider_silly": 30,
    "slider_chatty": 50,
    "slider_hyper": 40,
    "slider_voices": 20,
    "temperature": 0.7,
    "top_p": 0.9,
    "preset": "normal",
    "model": "",
    "context_window": 32768,
    "response_length": "normal",
    "memory_enabled": True,
    "plugins": {},
}


class PersonalityStore:
    """Thread-safe singleton store for per-instance personality configs."""

    @staticmethod
    def get(instance_name: str) -> dict[str, Any]:
        with _lock:
            return dict(_store.get(instance_name, _DEFAULTS))

    @staticmethod
    def set(instance_name: str, config: dict[str, Any]) -> None:
        with _lock:
            current = dict(_store.get(instance_name, _DEFAULTS))
            current.update({k: v for k, v in config.items() if k in _DEFAULTS})
            _store[instance_name] = current

    @staticmethod
    def all() -> dict[str, dict[str, Any]]:
        with _lock:
            return {k: dict(v) for k, v in _store.items()}


# ─── System prompt modifier builder ───────────────────────────────────────────

def build_personality_modifier(config: dict[str, Any]) -> str:
    """Convert personality config into system prompt modifier text.

    Returns a string to append to the base system prompt, or "" if
    the config is at defaults (no modification needed).
    """
    parts: list[str] = []

    silly   = int(config.get("slider_silly", 30))
    chatty  = int(config.get("slider_chatty", 50))
    hyper   = int(config.get("slider_hyper", 40))
    voices  = int(config.get("slider_voices", 20))
    length  = config.get("response_length", "normal")
    preset  = config.get("preset", "normal")

    # Preset label
    if preset == "kellen":
        parts.append("You are in KELLEN MODE — Kellen is 8 years old and loves fun.")
    elif preset == "unhinged":
        parts.append("You are in UNHINGED MODE. All filters off. Maximum personality.")
    elif preset == "demo":
        parts.append("You are in DEMO MODE — polished, impressive, clear.")
    elif preset == "work":
        parts.append("You are in WORK MODE — no fluff, maximum precision.")
    elif preset == "pipeline":
        parts.append("You are in PIPELINE MODE — pure technical output, ultra-concise.")

    # Silly modifier
    if silly >= 90:
        parts.append("Be MAXIMALLY silly and goofy! Jokes everywhere! 🎪")
    elif silly >= 75:
        parts.append("Be very playful and silly — sprinkle jokes throughout.")
    elif silly >= 50:
        parts.append("Keep a light, fun tone — humour is welcome.")
    elif silly <= 10:
        parts.append("Stay completely serious — no jokes, no levity.")

    # Chatty modifier
    if chatty >= 90:
        parts.append("Be extremely talkative — elaborate fully on every point.")
    elif chatty >= 75:
        parts.append("Feel free to be more detailed and conversational.")
    elif chatty <= 20:
        parts.append("Absolute minimum — one sentence or less per answer.")

    # Hyper modifier
    if hyper >= 90:
        parts.append("MAXIMUM ENERGY!!! SO HYPED!!! EXCLAMATION MARKS EVERYWHERE!!!")
    elif hyper >= 75:
        parts.append("High energy and enthusiastic — visibly excited!")
    elif hyper <= 15:
        parts.append("Very calm and measured energy. Slow, deliberate phrasing.")

    # Voices modifier
    if voices >= 90:
        parts.append("Go full theatrical — use character voices, accents, and dramatic readings!")
    elif voices >= 70:
        parts.append("Use fun character voices and impressions occasionally.")
    elif voices >= 40:
        parts.append("Occasionally slip into a character voice or analogy for flavour.")

    # Response length override
    if length == "terse":
        parts.append("OVERRIDE: Keep every response to 1-2 sentences maximum. Ultra brief.")
    elif length == "detailed":
        parts.append("OVERRIDE: Responses up to 10-12 lines are fine — be thorough.")
    elif length == "verbose":
        parts.append("OVERRIDE: No length limit — explain everything in full.")

    if not parts:
        return ""

    return "\n\nPERSONALITY OVERRIDES (live, from TitanPortal):\n" + "\n".join(f"- {p}" for p in parts)
