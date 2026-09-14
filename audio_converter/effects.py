from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from .core import FORMATS


@dataclass(frozen=True)
class EffectPreset:
    icon: str
    name_key: str
    description_key: str


@dataclass(frozen=True)
class CustomEffect:
    """A user-created voice preset stored outside the application folder."""

    key: str
    name: str
    pitch_semitones: float
    echo_percent: float
    bass_db: float
    tone_db: float


@dataclass(frozen=True)
class SoundAdjustments:
    """Temporary fine-tuning applied after the selected voice effect."""

    pitch_semitones: float = 0.0
    bass_db: float = 0.0
    treble_db: float = 0.0
    reverb_percent: float = 0.0


EFFECTS: dict[str, EffectPreset] = {
    "female": EffectPreset("♀", "effect_female", "effect_female_desc"),
    "male": EffectPreset("♂", "effect_male", "effect_male_desc"),
    "robot": EffectPreset("▣", "effect_robot", "effect_robot_desc"),
    "child": EffectPreset("★", "effect_child", "effect_child_desc"),
    "child_boy": EffectPreset("♙", "effect_child_boy", "effect_child_boy_desc"),
    "child_girl": EffectPreset("✿", "effect_child_girl", "effect_child_girl_desc"),
    "elder_male": EffectPreset("◈", "effect_elder_male", "effect_elder_male_desc"),
    "elder_female": EffectPreset("◇", "effect_elder_female", "effect_elder_female_desc"),
    "monster": EffectPreset("◆", "effect_monster", "effect_monster_desc"),
    "alien": EffectPreset("◎", "effect_alien", "effect_alien_desc"),
    "radio": EffectPreset("◉", "effect_radio", "effect_radio_desc"),
    "echo": EffectPreset("≋", "effect_echo", "effect_echo_desc"),
}


def custom_effects_path() -> Path:
    """Return the per-user settings file without modifying it."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "micxio" / "custom-effects.json"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _safe_effect_name(value: object) -> str:
    return " ".join(str(value).strip().split())[:48]


def load_custom_effects(path: Path | None = None) -> dict[str, CustomEffect]:
    """Load valid user presets. A damaged settings file is safely ignored."""
    settings_path = path or custom_effects_path()
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, list):
        return {}

    effects: dict[str, CustomEffect] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", ""))
        name = _safe_effect_name(item.get("name", ""))
        if not re.fullmatch(r"custom-[0-9a-f]{12}", key) or not name:
            continue
        effects[key] = CustomEffect(
            key=key,
            name=name,
            pitch_semitones=_clamp(item.get("pitch_semitones", 0), -12, 12),
            echo_percent=_clamp(item.get("reverb_percent", item.get("echo_percent", 0)), 0, 100),
            bass_db=_clamp(item.get("bass_db", 0), -12, 12),
            tone_db=_clamp(item.get("tone_db", 0), -12, 12),
        )
    return effects


def save_custom_effects(effects: Mapping[str, CustomEffect], path: Path | None = None) -> None:
    """Persist custom presets atomically so an interrupted save keeps the old file."""
    settings_path = path or custom_effects_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "key": effect.key,
            "name": effect.name,
            "pitch_semitones": effect.pitch_semitones,
            "reverb_percent": effect.echo_percent,
            "bass_db": effect.bass_db,
            "tone_db": effect.tone_db,
        }
        for effect in effects.values()
    ]
    temporary_path = settings_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(settings_path)


def new_custom_effect(name: str, pitch_semitones: float, echo_percent: float, tone_db: float,
                      key: str | None = None, *, bass_db: float = 0) -> CustomEffect:
    """Validate and create an in-memory custom effect."""
    cleaned_name = _safe_effect_name(name)
    if not cleaned_name:
        raise ValueError("A custom effect needs a name.")
    effect_key = key or f"custom-{uuid4().hex[:12]}"
    if not re.fullmatch(r"custom-[0-9a-f]{12}", effect_key):
        raise ValueError("Invalid custom effect key.")
    return CustomEffect(
        key=effect_key,
        name=cleaned_name,
        pitch_semitones=_clamp(pitch_semitones, -12, 12),
        echo_percent=_clamp(echo_percent, 0, 100),
        bass_db=_clamp(bass_db, -12, 12),
        tone_db=_clamp(tone_db, -12, 12),
    )


def build_effect_filter(effect: str, intensity: float,
                        custom_effects: Mapping[str, CustomEffect] | None = None) -> str:
    """Build a safe FFmpeg audio-filter chain for a named voice effect."""
    if custom_effects and effect in custom_effects:
        return build_custom_effect_filter(custom_effects[effect])
    if effect not in EFFECTS:
        raise ValueError(f"Unknown voice effect: {effect}")
    amount = max(0.0, min(1.0, intensity / 100.0))

    if effect == "female":
        pitch = 1.0 + 0.28 * amount
        return f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality"
    if effect == "male":
        pitch = 1.0 - 0.22 * amount
        return f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality"
    if effect == "child":
        pitch = 1.0 + 0.55 * amount
        return f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality"
    if effect == "child_boy":
        pitch = 1.0 + 0.34 * amount
        return f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality"
    if effect == "child_girl":
        pitch = 1.0 + 0.52 * amount
        return f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality"
    if effect == "elder_male":
        pitch = 1.0 - 0.24 * amount
        return (
            f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality,"
            f"lowpass=f={int(5800 - 1500 * amount)},tremolo=f={4.2 + amount * 1.8:.2f}:d={0.04 + amount * 0.08:.3f}"
        )
    if effect == "elder_female":
        pitch = 1.0 - 0.12 * amount
        return (
            f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality,"
            f"lowpass=f={int(6400 - 1400 * amount)},tremolo=f={4.8 + amount * 1.8:.2f}:d={0.035 + amount * 0.07:.3f}"
        )
    if effect == "monster":
        pitch = 1.0 - 0.38 * amount
        echo_decay = 0.12 + 0.28 * amount
        return (
            f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality,"
            f"acompressor=threshold=-20dB:ratio=5:attack=8:release=100,"
            f"aecho=0.8:0.65:55:{echo_decay:.3f}"
        )
    if effect == "robot":
        frequency = 28.0 + 52.0 * amount
        depth = 0.35 + 0.60 * amount
        return (
            f"highpass=f=90,lowpass=f=7500,tremolo=f={frequency:.2f}:d={depth:.3f},"
            "aecho=0.8:0.45:18|36:0.18|0.10"
        )
    if effect == "alien":
        depth = 0.18 + 0.30 * amount
        speed = 1.2 + 2.0 * amount
        return (
            f"chorus=0.7:0.9:45|55:{depth:.3f}|{depth * 0.8:.3f}:0.25|0.35:{speed:.3f}|{speed * 1.2:.3f},"
            f"tremolo=f={5.0 + 8.0 * amount:.2f}:d={0.15 + 0.35 * amount:.3f}"
        )
    if effect == "radio":
        lowpass = int(4200 - 1100 * amount)
        return (
            f"highpass=f=300,lowpass=f={lowpass},"
            "acompressor=threshold=-18dB:ratio=4:attack=5:release=50,volume=1.35"
        )
    delay = int(90 + 330 * amount)
    decay = 0.18 + 0.55 * amount
    return f"aecho=0.8:0.65:{delay}:{decay:.3f}"


def build_custom_effect_filter(effect: CustomEffect) -> str:
    """Build the filter chain for a named custom voice effect."""
    pitch = 2.0 ** (effect.pitch_semitones / 12.0)
    filters = [f"rubberband=pitch={pitch:.4f}:formant=shifted:pitchq=quality"]
    if abs(effect.bass_db) >= 0.1:
        filters.append(f"equalizer=f=180:t=q:w=1:g={effect.bass_db:.2f}")
    if abs(effect.tone_db) >= 0.1:
        filters.append(f"equalizer=f=5000:t=q:w=1:g={effect.tone_db:.2f}")
    if effect.echo_percent >= 1:
        decay = 0.08 + 0.62 * (effect.echo_percent / 100.0)
        filters.append(f"aecho=0.8:0.60:160:{decay:.3f}")
    return ",".join(filters)


def build_adjustment_filter(adjustments: SoundAdjustments) -> str:
    """Build optional post-effect controls for the main real-time sliders."""
    pitch = _clamp(adjustments.pitch_semitones, -12, 12)
    bass = _clamp(adjustments.bass_db, -12, 12)
    treble = _clamp(adjustments.treble_db, -12, 12)
    reverb = _clamp(adjustments.reverb_percent, 0, 100)
    filters: list[str] = []
    if abs(pitch) >= 0.1:
        filters.append(f"rubberband=pitch={2.0 ** (pitch / 12.0):.4f}:formant=shifted:pitchq=quality")
    if abs(bass) >= 0.1:
        filters.append(f"equalizer=f=180:t=q:w=1:g={bass:.2f}")
    if abs(treble) >= 0.1:
        filters.append(f"equalizer=f=5000:t=q:w=1:g={treble:.2f}")
    if reverb >= 1:
        decay = 0.08 + 0.62 * (reverb / 100.0)
        filters.append(f"aecho=0.8:0.60:160:{decay:.3f}")
    return ",".join(filters)


def unique_effect_output_path(
    source: Path,
    output_dir: Path,
    effect: str,
    output_format: str = "MP3",
) -> Path:
    extension = str(FORMATS[output_format]["extension"])
    candidate = output_dir / f"{source.stem}_{effect}{extension}"
    number = 2
    while candidate.exists() or candidate.resolve() == source.resolve():
        candidate = output_dir / f"{source.stem}_{effect}_{number}{extension}"
        number += 1
    return candidate
