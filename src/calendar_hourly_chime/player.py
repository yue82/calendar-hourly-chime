"""wav の再生。指定時刻ぴったりに鳴らすスケジュール再生に対応。"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .tts import ps_quote, run_powershell, to_windows_path

log = logging.getLogger(__name__)

# これ以上遅れた項目は鳴らさない
MAX_LATE_SEC = 3.0


@dataclass(frozen=True)
class Scheduled:
    path: Path
    at: float  # 再生開始の UNIX 時刻 (秒)
    label: str


def play_now(paths: list[Path], player: str) -> None:
    t = time.time()
    play_scheduled([Scheduled(p, t, p.name) for p in paths], player)


def play_scheduled(items: list[Scheduled], player: str) -> None:
    if not items:
        return
    if player == "windows":
        _play_windows(items)
    elif player == "paplay":
        _play_paplay(items)
    else:
        raise ValueError(f"未知の player: {player}")


def _play_windows(items: list[Scheduled]) -> None:
    # 1 プロセスで全項目を順に待って鳴らす。待ちは Windows 側の時計で行う
    # (WSL2 の時計はスリープ復帰後などにずれることがあるため)
    rows = ",\n".join(
        f"@({ps_quote(to_windows_path(i.path))}, {int(i.at * 1000)}, {ps_quote(i.label)})" for i in items
    )
    script = f"""
$items = @(
{rows}
)
foreach ($it in $items) {{
    $p = New-Object System.Media.SoundPlayer $it[0]
    $p.Load()
    $t = [DateTimeOffset]::FromUnixTimeMilliseconds($it[1])
    while (($ms = ($t - [DateTimeOffset]::UtcNow).TotalMilliseconds) -gt 0) {{
        if ($ms -gt 40) {{ Start-Sleep -Milliseconds ([int]($ms - 30)) }}
    }}
    $late = ([DateTimeOffset]::UtcNow - $t).TotalMilliseconds
    if ($late -gt {int(MAX_LATE_SEC * 1000)}) {{ "skip $($it[2]) late=$([int]$late)ms"; continue }}
    "play $($it[2]) late=$([int]$late)ms"
    $p.PlaySync()
    $p.Dispose()
}}
"""
    span = max(i.at for i in items) - time.time()
    out = run_powershell(script, timeout=max(span, 0) + 120)
    for line in out.splitlines():
        if line.strip():
            log.info("player: %s", line.strip())


def _play_paplay(items: list[Scheduled]) -> None:
    for i in items:
        wait = i.at - time.time()
        if wait > 0:
            time.sleep(wait)
        elif -wait > MAX_LATE_SEC:
            log.info("player: skip %s late=%dms", i.label, -wait * 1000)
            continue
        log.info("player: play %s", i.label)
        subprocess.run(["paplay", str(i.path)], check=True, timeout=120)
