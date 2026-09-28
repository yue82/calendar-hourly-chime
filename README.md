# hourly-chime

Google カレンダー (iCal) と連動する音声時報。WSL2 + Windows 用。

正時 N:00 に向けて次のタイミングで鳴らす。

| タイミング | 予定なし | 予定中 (音のみ) |
|---|---|---|
| 5分前 | 「N時5分前です。」+ N時台に始まる予定 | ポポポポポ |
| 2分前 | 「2分前です。」 | ポポ |
| 15秒前 | 「15秒前です。」 | ポーン |
| 正時 | ピピピポーン「N時です。」+ N+1時台に始まる予定 | ピピピポーン |

- 「予定中」= その時刻に終日以外の予定が入っている (全カレンダー対象)
- `busy_only` のカレンダーは予定名を読まず、時間枠の判定だけに使う
- `quiet_hours` の間は鳴らさない
- 休日 (`holiday` のカレンダーに条件に合う終日予定がある日) は `holiday.hours` の正時だけ、ピピピポーン「N時です。」+ 次の時報までに始まる予定 (`quiet_hours` より優先)

```
タスクスケジューラ (毎時 54:30) → conhost --headless wsl.exe → hourly-chime chime
  → ICS 取得 (キャッシュ) → 4 タイミング分の計画 → TTS + 時報音を wav に合成
  → PowerShell 1 プロセスが Windows の時計で各時刻まで待って再生
```

## セットアップ

```sh
uv sync
mkdir -p ~/.config/hourly-chime
cp config.example.yaml ~/.config/hourly-chime/config.yaml   # iCal URL などを編集
uv run hourly-chime demo                                     # 次の正時の時報を詰めて今すぐ鳴らす
uv run hourly-chime simulate --hours 24                      # この先の時報を確認
powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w scripts/install_task.ps1)"
```

削除は `install_task.ps1 -Uninstall`。

## コマンド

| コマンド | 内容 |
|---|---|
| `chime [--dry-run] [--at 2026-09-28T14:00]` | 次の正時の時報 (スケジューラ用) |
| `demo [--sound-only] [--at ...]` | 次の正時の時報を間を詰めて今すぐ鳴らす |
| `simulate [--hours N] [--all]` | この先の時報を一覧表示 |
| `events [--hours N] [--refresh]` | 予定一覧 |
| `say TEXT` | 音声確認 |

- 設定: `~/.config/hourly-chime/config.yaml` (書式は `config.example.yaml`)
- ログ: `~/.local/state/hourly-chime/chime.log`
- キャッシュ: `~/.cache/hourly-chime/` (ICS・合成済み wav)
