"""wav の再生。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .tts import ps_quote, run_powershell, to_windows_path


def play(paths: list[Path], player: str) -> None:
    if not paths:
        return
    if player == "windows":
        files = ", ".join(ps_quote(to_windows_path(p)) for p in paths)
        run_powershell(f"foreach ($p in @({files})) {{ (New-Object System.Media.SoundPlayer $p).PlaySync() }}")
    elif player == "paplay":
        for p in paths:
            subprocess.run(["paplay", str(p)], check=True, timeout=60)
    else:
        raise ValueError(f"未知の player: {player}")
