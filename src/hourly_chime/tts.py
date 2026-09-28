"""音声合成。結果の wav はテキスト単位でキャッシュする。"""

from __future__ import annotations

import base64
import hashlib
import logging
import subprocess
import time
from pathlib import Path

import requests

from .config import CACHE_DIR, TTSConfig

log = logging.getLogger(__name__)

TTS_CACHE = CACHE_DIR / "tts"
CACHE_TTL_DAYS = 30


def run_powershell(script: str, timeout: float = 60) -> None:
    # 日本語をコマンドラインで渡すと文字化けするので UTF-16LE base64 で渡す
    # 進捗表示が CLIXML として stderr に漏れるので抑止する
    script = "$ProgressPreference = 'SilentlyContinue'\n" + script
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
        check=True,
        timeout=timeout,
        stdout=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        # WSL の cwd が UNC だと警告が出るので Windows 側のディレクトリから起動する
        cwd="/mnt/c",
    )


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def to_windows_path(p: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(p)], check=True, capture_output=True, text=True).stdout.strip()


def _synth_sapi(text: str, out: Path, cfg: TTSConfig) -> None:
    run_powershell(
        f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice({ps_quote(cfg.sapi_voice)})
$s.Rate = {int(cfg.sapi_rate)}
$s.SetOutputToWaveFile({ps_quote(to_windows_path(out))})
$s.Speak({ps_quote(text)})
$s.Dispose()
"""
    )


def _synth_voicevox(text: str, out: Path, cfg: TTSConfig) -> None:
    params = {"speaker": cfg.voicevox_speaker}
    q = requests.post(f"{cfg.voicevox_url}/audio_query", params={**params, "text": text}, timeout=10)
    q.raise_for_status()
    query = q.json()
    query["speedScale"] = cfg.voicevox_speed
    r = requests.post(f"{cfg.voicevox_url}/synthesis", params=params, json=query, timeout=60)
    r.raise_for_status()
    out.write_bytes(r.content)


def _cache_key(engine: str, text: str, cfg: TTSConfig) -> str:
    if engine == "voicevox":
        opts = f"{cfg.voicevox_speaker}|{cfg.voicevox_speed}"
    else:
        opts = f"{cfg.sapi_voice}|{cfg.sapi_rate}"
    return hashlib.sha256(f"{engine}|{opts}|{text}".encode()).hexdigest()[:16]


def synthesize(text: str, cfg: TTSConfig) -> Path:
    """text を wav にして返す。voicevox が失敗したら sapi にフォールバック。"""
    TTS_CACHE.mkdir(parents=True, exist_ok=True)
    engines = [cfg.engine] + (["sapi"] if cfg.engine != "sapi" else [])
    last_err: Exception | None = None
    for engine in engines:
        out = TTS_CACHE / f"{engine}-{_cache_key(engine, text, cfg)}.wav"
        if out.exists():
            out.touch()
            return out
        tmp = out.with_suffix(".tmp.wav")
        try:
            if engine == "voicevox":
                _synth_voicevox(text, tmp, cfg)
            elif engine == "sapi":
                _synth_sapi(text, tmp, cfg)
            else:
                raise ValueError(f"未知の tts.engine: {engine}")
            tmp.replace(out)
            return out
        except Exception as e:
            log.warning("TTS %s に失敗: %s", engine, e)
            tmp.unlink(missing_ok=True)
            last_err = e
    raise RuntimeError("全ての TTS エンジンが失敗しました") from last_err


def prune_cache() -> None:
    if not TTS_CACHE.exists():
        return
    limit = time.time() - CACHE_TTL_DAYS * 86400
    for p in TTS_CACHE.glob("*.wav"):
        if p.stat().st_mtime < limit:
            p.unlink(missing_ok=True)
