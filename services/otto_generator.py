# -*- coding: utf-8 -*-
"""Python implementation of the default Otto HZYS audio generator.

The source project builds a sequence of short pinyin WAV clips and optional
整句原声 MP3 clips.  This module intentionally keeps only that construction
logic (plus whole-file reversal); browser effects such as EQ, compressor,
delay, reverb and mixing are not implemented.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf
from pypinyin import Style, lazy_pinyin, pinyin


BASE_DIR = Path(__file__).resolve().parents[1]
OTTO_ASSET_DIR = BASE_DIR / "assets" / "otto"
TOKENS_DIR = OTTO_ASSET_DIR / "tokens"
YSDD_TOKENS_DIR = OTTO_ASSET_DIR / "ysdd_tokens"
TOKENS_JSON = OTTO_ASSET_DIR / "tokens.json"
YSDD_JSON = OTTO_ASSET_DIR / "ysdd.json"
CHINGLISH_JSON = OTTO_ASSET_DIR / "chinglish.json"

_MAX_AUDIO_SECONDS = 60.0
_WORD_RE = re.compile(r"^[A-Z]+$")
_ASCII_LETTERS_RE = re.compile(r"[A-Za-z]+")
_SUPPORTED_SINGLE_RE = re.compile(r"^[.0-9A-Za-z\u4e00-\u9fff]$")


@dataclass(frozen=True)
class _TextUnit:
    value: str
    pinyin: str
    is_english_word: bool = False


@dataclass(frozen=True)
class _AudioToken:
    kind: str  # "token", "ysdd", or "blank"
    name: str


class OttoAssetError(RuntimeError):
    """Raised when the bundled Otto resources cannot be loaded."""


class _OttoAssets:
    def __init__(self) -> None:
        try:
            self.token_names = {
                str(item).strip().lower()
                for item in json.loads(TOKENS_JSON.read_text(encoding="utf-8"))
                if str(item).strip()
            }
            raw_ysdd = json.loads(YSDD_JSON.read_text(encoding="utf-8"))
            raw_chinglish = json.loads(CHINGLISH_JSON.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - exercised by deployment errors
            raise OttoAssetError(f"无法加载 Otto 音频资源: {exc}") from exc

        if not self.token_names or not isinstance(raw_ysdd, dict):
            raise OttoAssetError("Otto 音频资源不完整")

        # The web implementation keeps several alternate recordings for a
        # number of pinyin tokens in subdirectories.  With the default
        # settings it randomly chooses one of those recordings, or the base
        # token with the same probability.  Keep that behavior here instead
        # of always using the first/root clip.
        self.token_subdir_files: dict[str, dict[str, list[Path]]] = {}
        if TOKENS_DIR.is_dir():
            for subdir in sorted(TOKENS_DIR.iterdir()):
                if not subdir.is_dir():
                    continue
                for path in subdir.glob("*.wav"):
                    # Alternate recordings are named like ``a_0001.wav`` or
                    # ``dai_0004.wav``.  The numeric suffix identifies the
                    # recording, not a different pinyin token.
                    match = re.fullmatch(r"(.+)_\d+", path.stem)
                    token_name = (match.group(1) if match else path.stem).lower()
                    by_dir = self.token_subdir_files.setdefault(token_name, {})
                    by_dir.setdefault(subdir.name, []).append(path)

        self.ysdd_by_pinyin: dict[tuple[str, ...], str] = {}
        self.ysdd_by_joined_pinyin: dict[str, str] = {}
        self.ysdd_by_filename: dict[str, str] = {}
        for filename, phrases in raw_ysdd.items():
            filename = str(filename).strip()
            if not filename or not isinstance(phrases, list):
                continue
            for phrase in phrases:
                phrase = str(phrase)
                py = tuple(_normal_pinyin_list(phrase))
                if py:
                    # The web implementation's Map.set() keeps the last
                    # duplicate mapping, so assignment here is intentional.
                    self.ysdd_by_pinyin[py] = filename
                    self.ysdd_by_joined_pinyin["".join(py)] = filename
                    self.ysdd_by_filename[filename] = phrase

        self.chinglish: dict[str, str] = {
            str(key).upper(): str(value)
            for key, value in (raw_chinglish or {}).items()
        }
        self.chinglish_tokens: dict[str, list[str]] = {
            key: [part.lower() for part in re.findall(r"[A-Z][a-z]*", value)]
            for key, value in self.chinglish.items()
        }
        self._matches_by_last: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for py, filename in self.ysdd_by_pinyin.items():
            self._matches_by_last.setdefault(py[-1], []).append((py, filename))
        for matches in self._matches_by_last.values():
            # The JS implementation evaluates short matches first and lets an
            # equal-score later match replace the prior one.
            matches.sort(key=lambda item: len(item[0]))

    def resolve_audio_path(self, token: _AudioToken) -> Path:
        if token.kind == "ysdd":
            path = YSDD_TOKENS_DIR / f"{token.name}.mp3"
            if path.is_file():
                return path
            # Keep the web implementation's fallback to a same-named WAV.
            wav_path = TOKENS_DIR / f"{token.name}.wav"
            if wav_path.is_file():
                return wav_path
        elif token.kind == "token":
            name = token.name.lower()
            base_path = TOKENS_DIR / f"{name}.wav"
            alternates = self.token_subdir_files.get(name, {})
            # Equivalent to the browser's Math.floor(random() * (n + 1)):
            # each available alternate directory is represented once, and
            # the final slot selects the root recording.
            if alternates:
                selected = random.randrange(len(alternates) + 1)
                if selected < len(alternates):
                    candidates = alternates[sorted(alternates)[selected]]
                    return random.choice(candidates)
            if base_path.is_file():
                return base_path
        else:
            path = TOKENS_DIR / "_.wav"
            if path.is_file():
                return path
        fallback = TOKENS_DIR / "_.wav"
        if fallback.is_file():
            return fallback
        raise OttoAssetError(f"找不到 Otto 音频片段: {token.kind}/{token.name}")


_ASSETS: _OttoAssets | None = None
_ASSETS_LOCK = threading.RLock()


def _get_assets() -> _OttoAssets:
    global _ASSETS
    with _ASSETS_LOCK:
        if _ASSETS is None:
            _ASSETS = _OttoAssets()
        return _ASSETS


def _normal_pinyin_list(text: str) -> list[str]:
    """Return the same untoned pinyin shape used by the web project."""
    values = lazy_pinyin(
        text,
        style=Style.NORMAL,
        strict=False,
        errors=lambda item: list(item),
    )
    result: list[str] = []
    for value in values:
        value = str(value).strip().lower().replace("ü", "v")
        if value:
            result.append(value)
    return result


def _normal_pinyin(value: str) -> str:
    values = _normal_pinyin_list(value)
    return values[0] if values else value.lower()


def _split_text(text: str) -> list[str]:
    """Match the webpage's word-preserving ASCII split and char split."""
    parts: list[str] = []
    for part in re.split(r"([A-Za-z]+)", text):
        if not part:
            continue
        if _ASCII_LETTERS_RE.fullmatch(part):
            parts.append(part.upper())
            continue
        # The webpage filters everything except dot, digits, ASCII letters and
        # CJK characters.  Whitespace and ordinary punctuation therefore do
        # not become audio tokens.
        cleaned = re.sub(r"[^.0-9A-Za-z\u4e00-\u9fff]+", " ", part.upper())
        parts.extend(ch for ch in cleaned if ch != " ")
    return parts


def _to_text_units(text: str) -> list[_TextUnit]:
    units: list[_TextUnit] = []
    for value in _split_text(text):
        if len(value) > 1 and _WORD_RE.fullmatch(value):
            units.append(_TextUnit(value=value, pinyin=value.lower(), is_english_word=True))
        else:
            units.append(_TextUnit(value=value, pinyin=_normal_pinyin(value)))
    return units


def _ysdd_parse(units: list[_TextUnit], assets: _OttoAssets) -> list[_TextUnit | _AudioToken]:
    """Select maximum-coverage original clips using the web DP algorithm."""
    # dp[i] stores the best token sequence for units[:i], with matched Chinese
    # character coverage as score.  A normal character advances by one and a
    # YSDD match advances by its full phrase length.
    dp: list[tuple[int, list[_TextUnit | _AudioToken]] | None] = [None] * (len(units) + 1)
    dp[0] = (0, [])
    for end in range(1, len(units) + 1):
        unit = units[end - 1]
        prior = dp[end - 1]
        if prior is not None:
            score, sequence = prior
            dp[end] = (score, sequence + [unit])

        for py, filename in assets._matches_by_last.get(unit.pinyin, ()):
            length = len(py)
            start = end - length
            if start < 0 or dp[start] is None:
                continue
            if tuple(item.pinyin for item in units[start:end]) != py:
                continue
            prev_score, prev_sequence = dp[start]
            candidate = (prev_score + length, prev_sequence + [_AudioToken("ysdd", filename)])
            if dp[end] is None or candidate[0] >= dp[end][0]:
                dp[end] = candidate

    return (dp[-1] or (0, []))[1]


def _expand_units(items: Iterable[_TextUnit | _AudioToken], assets: _OttoAssets) -> list[_AudioToken]:
    result: list[_AudioToken] = []
    for item in items:
        if isinstance(item, _AudioToken):
            result.append(item)
            continue

        if item.is_english_word:
            filename = assets.ysdd_by_joined_pinyin.get(item.pinyin.lower())
            if filename is None:
                filename = assets.ysdd_by_pinyin.get((item.pinyin,))
            if filename:
                result.append(_AudioToken("ysdd", filename))
                continue
            for char in item.value:
                mapped = assets.chinglish_tokens.get(char.upper())
                if mapped:
                    for py in mapped:
                        result.append(_AudioToken("token", py))
                else:
                    result.append(_AudioToken("token", char.lower()))
            continue

        # The webpage passes punctuation through chinglish when available;
        # unsupported punctuation was filtered before this stage.
        if item.value.upper() in assets.chinglish_tokens:
            for py in assets.chinglish_tokens[item.value.upper()]:
                result.append(_AudioToken("token", py))
        else:
            result.append(_AudioToken("token", item.pinyin))
    return result


def _compact_missing_tokens(tokens: Iterable[_AudioToken], assets: _OttoAssets) -> list[_AudioToken]:
    """Mirror the web fallback: consecutive missing tokens become one blank."""
    result: list[_AudioToken] = []
    for token in tokens:
        if token.kind == "token" and token.name.lower() not in assets.token_names:
            if not result or result[-1].kind != "blank":
                result.append(_AudioToken("blank", "_"))
            continue
        result.append(token)
    return result


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
    if data.size == 0 or sample_rate <= 0:
        raise OttoAssetError(f"音频片段为空: {path.name}")
    return np.asarray(data, dtype=np.float32), int(sample_rate)


def _resample(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(data) <= 1:
        return data
    target_length = max(1, int(round(len(data) * target_rate / source_rate)))
    old_x = np.linspace(0.0, 1.0, num=len(data), endpoint=True)
    new_x = np.linspace(0.0, 1.0, num=target_length, endpoint=True)
    channels = [np.interp(new_x, old_x, data[:, channel]) for channel in range(data.shape[1])]
    return np.stack(channels, axis=1).astype(np.float32, copy=False)


def _normalize_channels(data: np.ndarray, channels: int = 2) -> np.ndarray:
    if data.shape[1] == channels:
        return data
    if data.shape[1] == 1 and channels == 2:
        return np.repeat(data, 2, axis=1)
    if channels == 1:
        # Avoid a level-changing mix: retain the first channel when a mono
        # output is required.  The default output is stereo, so this is rare.
        return data[:, :1]
    if data.shape[1] > channels:
        return data[:, :channels]
    padding = np.repeat(data[:, -1:], channels - data.shape[1], axis=1)
    return np.concatenate([data, padding], axis=1)


def generate_otto_audio(
    text: str,
    *,
    reverse: bool = False,
    output_dir: str | os.PathLike[str] | None = None,
    max_seconds: float = _MAX_AUDIO_SECONDS,
) -> tuple[str, float]:
    """Generate an Otto WAV and return ``(absolute_path, actual_duration)``."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("待生成文本不能为空")
    try:
        requested_max_seconds = float(max_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("???????????") from exc
    if not np.isfinite(requested_max_seconds) or requested_max_seconds <= 0:
        raise ValueError("?????????? 0")
    # The voice interface and this plugin are intentionally capped at 60 s.
    max_seconds = min(requested_max_seconds, _MAX_AUDIO_SECONDS)

    assets = _get_assets()
    units = _to_text_units(text)
    if not units:
        raise ValueError("待生成文本中没有可生成的字符")

    sequence = _compact_missing_tokens(
        _expand_units(_ysdd_parse(units, assets), assets),
        assets,
    )
    if not sequence:
        raise ValueError("没有可用的 Otto 音频片段")

    chunks: list[np.ndarray] = []
    target_rate: int | None = None
    for token in sequence:
        path = assets.resolve_audio_path(token)
        data, sample_rate = _read_audio(path)
        if target_rate is None:
            target_rate = sample_rate
        data = _resample(data, sample_rate, target_rate)
        chunks.append(_normalize_channels(data, 2))

    assert target_rate is not None
    audio = np.concatenate(chunks, axis=0)
    if reverse:
        audio = audio[::-1, :]

    limit_frames = max(1, int(round(float(max_seconds) * target_rate)))
    if len(audio) > limit_frames:
        audio = audio[:limit_frames]

    destination = Path(output_dir) if output_dir is not None else Path(os.getenv("TEMP", "."))
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"otto_{uuid.uuid4().hex}.wav"
    sf.write(str(path), audio, target_rate, format="WAV", subtype="PCM_16")
    info = sf.info(str(path))
    actual_duration = float(info.frames) / float(info.samplerate)
    if actual_duration <= 0:
        try:
            path.unlink()
        except OSError:
            pass
        raise ValueError("生成的音频为空")
    return os.path.abspath(str(path)), actual_duration


__all__ = ["generate_otto_audio", "OttoAssetError"]
