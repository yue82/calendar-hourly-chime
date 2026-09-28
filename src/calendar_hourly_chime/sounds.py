"""時報音の合成と、音 + 読み上げを 1 つの wav に繋ぐ処理。

音は NHK 式時報に倣う: ピッ = 440Hz 0.1秒、ポーン = 880Hz の減衰音。
"""

from __future__ import annotations

import hashlib
import math
import wave
from array import array
from pathlib import Path

from .config import CACHE_DIR, SoundOverride

RATE = 24000  # VOICEVOX の既定出力と揃える
SLOT_CACHE = CACHE_DIR / "slots"
VERSION = "v3"  # 音を変えたら上げる (キャッシュ無効化)


def _tone(freq: float, dur: float, tau: float | None, amp: float, harmonic: float = 0.0) -> list[float]:
    n = int(dur * RATE)
    attack, release = int(0.003 * RATE), int(0.01 * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        env = math.exp(-t / tau) if tau else 1.0
        env *= min(1.0, i / attack, (n - i) / release)
        v = math.sin(2 * math.pi * freq * t) + harmonic * math.sin(4 * math.pi * freq * t)
        out.append(amp * env * v / (1 + harmonic))
    return out


def _pip() -> list[float]:
    return _tone(440, 0.1, None, 0.45)


def _poon() -> list[float]:
    return _tone(880, 2.5, 0.7, 0.5, harmonic=0.15)


def _po() -> list[float]:
    return _tone(880, 0.08, 0.03, 0.5, harmonic=0.15)


def _pi_hi() -> list[float]:
    return _tone(1175, 0.15, 0.1, 0.45, harmonic=0.15)


def _pin() -> list[float]:
    return _tone(1568, 0.5, 0.12, 0.4, harmonic=0.3)


def _poon_lo() -> list[float]:
    return _tone(784, 2.0, 0.6, 0.5, harmonic=0.15)


def _sequence(parts: list[tuple[float, list[float]]]) -> list[float]:
    total = max(int(at * RATE) + len(s) for at, s in parts)
    buf = [0.0] * total
    for at, s in parts:
        o = int(at * RATE)
        for i, v in enumerate(s):
            buf[o + i] += v
    return buf


# 名前 -> (合成関数, アンカー秒, 読み上げ開始秒)。アンカー秒の位置が指定時刻ちょうどに来るよう再生する。
# 読み上げ開始秒が None なら音が鳴り終わってから読む (余韻の長い音は余韻に重ねて読む)
SOUNDS = {
    "pipipipoon": (lambda: _sequence([(0, _pip()), (1, _pip()), (2, _pip()), (3, _poon())]), 3.0, 4.2),
    "pipoon": (lambda: _sequence([(0, _pi_hi()), (0.3, _poon_lo())]), 0.3, 1.3),
    "popopopopo": (lambda: _sequence([(i * 0.1, _po()) for i in range(5)]), 0.0, None),
    "popo": (lambda: _sequence([(i * 0.1, _po()) for i in range(2)]), 0.0, None),
    "poon": (lambda: _poon(), 0.0, None),
    "pin": (lambda: _pin(), 0.0, None),
    "pinpin": (lambda: _sequence([(i * 0.22, _pin()) for i in range(2)]), 0.0, None),
    "pinpinpin": (lambda: _sequence([(i * 0.22, _pin()) for i in range(3)]), 0.0, None),
}


def _read_wav(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"16bit 以外の wav は未対応: {path}")
        ch, rate = w.getnchannels(), w.getframerate()
        a = array("h", w.readframes(w.getnframes()))
    mono = [sum(a[i : i + ch]) / ch / 32768 for i in range(0, len(a), ch)]
    if rate == RATE or not mono:
        return mono
    # 線形補間でリサンプル
    n = int(len(mono) * RATE / rate)
    out = []
    for i in range(n):
        x = i * rate / RATE
        j = int(x)
        k = min(j + 1, len(mono) - 1)
        out.append(mono[j] + (mono[k] - mono[j]) * (x - j))
    return out


def _write_wav(path: Path, samples: list[float]) -> None:
    pcm = array("h", (max(-32767, min(32767, int(v * 32767))) for v in samples))
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def _resolve(sound: str, overrides: dict[str, SoundOverride]) -> tuple[list[float], float, float | None, str]:
    """(サンプル, アンカー秒, 読み上げ開始秒, キャッシュ用の識別子)。"""
    if o := overrides.get(sound):
        return _read_wav(o.path), o.anchor, None, f"{o.path}|{o.path.stat().st_mtime_ns}|{o.anchor}"
    if sound not in SOUNDS:
        raise ValueError(f"未知の音: {sound} (組み込み: {', '.join(SOUNDS)}。sounds で wav を指定できる)")
    gen, anchor, voice_at = SOUNDS[sound]
    return gen(), anchor, voice_at, sound


def compose(
    sound: str | None, voice: Path | None, overrides: dict[str, SoundOverride] | None = None
) -> tuple[Path, float]:
    """音 → 読み上げ の wav を作り、(パス, アンカー秒) を返す。"""
    if sound is None and voice is None:
        raise ValueError("sound も voice も無い")
    samples, anchor, voice_at, ident = _resolve(sound, overrides or {}) if sound else ([], 0.0, None, "")
    key = hashlib.sha256(f"{VERSION}|{ident}|{voice.name if voice else ''}".encode()).hexdigest()[:16]
    out = SLOT_CACHE / f"{key}.wav"
    if out.exists():
        out.touch()
        return out, anchor
    SLOT_CACHE.mkdir(parents=True, exist_ok=True)
    if sound and voice:
        if voice_at is None:
            voice_at = len(samples) / RATE + 0.3
        buf = _sequence([(0, samples), (voice_at, _read_wav(voice))])
    elif sound:
        buf = samples
    else:
        buf = _read_wav(voice)
    _write_wav(out, buf)
    return out, anchor


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()
