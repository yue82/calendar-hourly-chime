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
- 正時始まりでない予定には、その 2分前「2分前です。」・15秒前「15秒前です。」・開始時 ピピピポーン「H時M分です。(予定名)です。」を鳴らす。鳴らす時刻に他の予定が入っていればその回は鳴らさない
- `busy_only` のカレンダーは予定名を読まず、時間枠の判定だけに使う
- `quiet_hours` の間は鳴らさない
- 休日 (`holiday` のカレンダーに条件に合う終日予定がある日) は `holiday.hours` の正時だけ、ピピピポーン「N時です。」+ 次の時報までに始まる予定 (`quiet_hours` より優先)
- `off_days` の曜日と、`off_days.title` に合う終日予定がある日は一切鳴らさない

```
タスクスケジューラ (5 分ごと x4:00/x9:00) → conhost --headless wsl.exe → hourly-chime chime
  → ICS 取得 (キャッシュ) → 起動 30 秒後からの 5 分間の計画 → TTS + 時報音を wav に合成
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

空き時間情報しか共有されていないカレンダー (会社アカウント等) は、共有先のアカウントで
`gas/busy_ics.gs` を Apps Script のウェブアプリとしてデプロイし、その URL を `busy_only: true` で登録する
(手順はファイル先頭のコメント)。

## コマンド

| コマンド | 内容 |
|---|---|
| `chime [--dry-run] [--all] [--at 2026-09-28T13:54]` | この先 5 分間の時報 (スケジューラ用) |
| `demo [--sound-only] [--at ...]` | 次の正時の時報を間を詰めて今すぐ鳴らす |
| `simulate [--hours N] [--all]` | この先の時報を一覧表示 |
| `events [--hours N] [--refresh]` | 予定一覧 |
| `say TEXT` | 音声確認 |

- 設定: `~/.config/hourly-chime/config.yaml` (書式は `config.example.yaml`)
- ログ: `~/.local/state/hourly-chime/chime.log`
- キャッシュ: `~/.cache/hourly-chime/` (ICS・合成済み wav)
