"""Windows の「応答不可」を予定に合わせて切り替える。

Windows 11 には公開 API が無いので:
- 状態の読み取り: 非公開の WNF (WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED) を読む (0 = オフ)
- 切り替え: UI Automation でタスクバーの時計を押して通知センターを開き、ベルのボタン (DoNotDisturbButton)
  を切り替えて閉じる (通知センターを開いた直後はこのボタンにフォーカスが当たる)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import STATE_DIR
from .ical import Event
from .tts import run_powershell

log = logging.getLogger(__name__)

STATE = STATE_DIR / "dnd.json"

_READ = r"""
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class Wnf {
    [DllImport("ntdll.dll")]
    public static extern int NtQueryWnfStateData(ref ulong StateName, IntPtr TypeId, IntPtr Scope,
        out uint ChangeStamp, byte[] Buffer, ref uint BufferSize);
}
"@
$name = [UInt64]"0x0D83063EA3BF1C75"
$buf = New-Object byte[] 4; $size = [uint32]4; $stamp = [uint32]0
$st = [Wnf]::NtQueryWnfStateData([ref]$name, [IntPtr]::Zero, [IntPtr]::Zero, [ref]$stamp, $buf, [ref]$size)
if ($st -ne 0) { "error $st" } else { "value " + [BitConverter]::ToInt32($buf, 0) }
"""

_SET = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
$A = [Windows.Automation.AutomationElement]; $Scope = [Windows.Automation.TreeScope]
function Cond($p, $v) { New-Object Windows.Automation.PropertyCondition($p, $v) }
$tray = $A::RootElement.FindFirst($Scope::Children, (Cond $A::ClassNameProperty "Shell_TrayWnd"))
# 時計のボタン (応答不可のオン/オフに関係なく常にあり、押すと通知センターが開く)
$btn = $tray.FindFirst($Scope::Descendants, (Cond $A::ClassNameProperty "SystemTray.OmniButton"))
if (-not $btn) { "error tray button not found"; exit }
$btn.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
$f = $null
for ($i = 0; $i -lt 30 -and -not $f; $i++) {
  Start-Sleep -Milliseconds 100
  $e = $A::FocusedElement
  if ($e -and $e.Current.AutomationId -eq "DoNotDisturbButton") { $f = $e }
}
if (-not $f) { "error do-not-disturb button not focused"; exit }
$tp = $f.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
if ($tp.Current.ToggleState.ToString() -ne "__WANT__") { $tp.Toggle(); Start-Sleep -Milliseconds 300 }
"state " + $tp.Current.ToggleState
[System.Windows.Forms.SendKeys]::SendWait("{ESC}")
"""


def is_on() -> bool | None:
    """応答不可がオンか。読めなければ None。"""
    try:
        out = run_powershell(_READ, timeout=30).strip()
    except Exception as e:
        log.warning("応答不可の状態を読めません: %s", e)
        return None
    if not out.startswith("value "):
        log.warning("応答不可の状態を読めません: %s", out)
        return None
    return out.split()[1] != "0"


def set_on(on: bool) -> bool:
    """応答不可をオン/オフにする。成功したら True。"""
    want = "On" if on else "Off"
    try:
        out = run_powershell(_SET.replace("__WANT__", want), timeout=30).strip()
    except Exception as e:
        log.warning("応答不可を切り替えられません: %s", e)
        return False
    if out != f"state {want}":
        log.warning("応答不可を切り替えられません: %s", out)
        return False
    log.info("応答不可を%sにしました", "オン" if on else "オフ")
    return True


@dataclass
class DndState:
    managed: bool = False  # このプログラムがオンにした
    last_day: str | None = None  # 最後に確認した日 (その日最初の確認かどうか)
    override_until: str | None = None  # 予定中に手動でオフにされた予定の終わり (それまではオンにしない)


def load(path: Path = STATE) -> DndState:
    try:
        return DndState(**json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError, TypeError):
        return DndState()


def save(st: DndState, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(st), ensure_ascii=False), encoding="utf-8")


def decide(
    now: datetime, busy: Event | None, current_on: bool, st: DndState, first_run_off: bool
) -> tuple[bool | None, DndState, str]:
    """(切り替え先 (None なら何もしない), 新しい状態, 理由)。"""
    st = DndState(**asdict(st))
    today = now.date().isoformat()
    if st.override_until and now >= datetime.fromisoformat(st.override_until):
        st.override_until = None

    if st.last_day != today:
        st.last_day = today
        if first_run_off and busy is None:
            st.managed, st.override_until = False, None
            return (False if current_on else None), st, "その日最初の確認 (予定なし)"

    if busy is not None:
        if st.override_until:
            return None, st, "予定中に手動でオフにされた"
        if current_on:
            return None, st, "予定中 (オン済み)"
        if st.managed:
            # オンにしたのにオフになっている = 手動でオフにされた。この予定の間はオンにしない
            st.managed, st.override_until = False, busy.end.isoformat()
            return None, st, "予定中に手動でオフにされた"
        st.managed = True
        return True, st, f"予定開始 ({busy.calendar}: {busy.title})"

    if st.managed:
        st.managed = False
        return (False if current_on else None), st, "予定終了"
    return None, st, "予定なし"
