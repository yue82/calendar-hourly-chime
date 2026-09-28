# hourly-chime

Google カレンダー (iCal) と連動する音声時報。WSL2 + Windows 用。

正時 N:00 に向けて次のタイミングで鳴らす。N:00 に始まる予定が無ければ正時のみ。

| タイミング | 予定なし | 予定中 (音のみ) |
|---|---|---|
| 5分前 | 「N時5分前です。」+ N時台に始まる予定 | ポポポポポ |
| 2分前 | 「2分前です。」 | ポポ |
| 15秒前 | 「15秒前です。」 | ポーン |
| 正時 | ピピピポーン「N時です。」+ N+1時台に始まる予定 | ピピピポーン |

- 「予定中」= その時刻に終日以外の予定が入っている (全カレンダー対象)
- 正時始まりでない予定には、その 2分前「2分前です。」・15秒前「15秒前です。」・開始時 ピピピポーン「H時M分です。(予定名)です。」を鳴らす。鳴らす時刻に他の予定が入っていればその回は鳴らさない
- `busy_only` のカレンダーや名前の無い予定は、予定名の代わりに「予定があります」「予定の時間です」と知らせる
- `quiet_hours` の間は鳴らさない
- 休日 (`holiday` のカレンダーに条件に合う終日予定がある日) は `holiday.hours` の正時だけ、ピピピポーン「N時です。」+ 次の時報までに始まる予定 (`quiet_hours` より優先)
- `off_days` の曜日と、`off_days.title` に合う終日予定がある日は一切鳴らさない

### カウントダウン

指定時刻に向けて 30/20/10/5/2/1 分前に「15時30分まで、あと30分です。」、時刻ちょうどにピピピポーン「15時30分です。」。
`quiet_hours`・休日・`off_days` に関係なく鳴らし、10 秒以内に重なる時報は鳴らさない。

- コマンド: `hourly-chime countdown 15:30` (`1530`、`+45` = 45 分後、`--offsets 30,10,5`)。
  一覧 `--list`、取り消し `--cancel ID|all`
- カレンダー: `countdown: true` を付けたカレンダーの全予定、または予定名が `countdown.title` (既定 `^⏰`)
  に合う予定の開始時刻。例: 「⏰出発」
  (`⏰` の予定には通常の予定 Cue は鳴らさず、案内では印を外して読む。専用カレンダーの予定は時報と独立で、予定中・案内・予定 Cue のどれにも使わない)
- Google カレンダーの「タスク」は API で時刻が取れない (日付のみ) ので使えない

```
タスクスケジューラ (5 分ごと x4:00/x9:00) → conhost --headless wsl.exe → hourly-chime chime
  → ICS 取得 (キャッシュ) → 起動 30 秒後からの 5 分間の計画 → TTS + 時報音を wav に合成
  → PowerShell 1 プロセスが Windows の時計で各時刻まで待って再生
```

## セットアップ

```sh
uv sync
mkdir -p ~/.config/hourly-chime
cp config.example.yaml ~/.config/hourly-chime/config.yaml   # 普段の設定
install -m 600 secrets.example.yaml ~/.config/hourly-chime/secrets.yaml  # 非公開 URL・キー
uv run hourly-chime demo                                     # 次の正時の時報を詰めて今すぐ鳴らす
uv run hourly-chime simulate --hours 24                      # この先の時報を確認
powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w scripts/install_task.ps1)"
```

削除は `install_task.ps1 -Uninstall`。

空き時間情報しか共有されていないカレンダー (会社アカウント等) は、共有先のアカウントで
`gas/busy_ics.gs` を Apps Script のウェブアプリとしてデプロイし、その URL を secrets.yaml に書いて `busy_only: true` で登録する
(手順はファイル先頭のコメント)。

## コマンド

| コマンド | 内容 |
|---|---|
| `chime [--dry-run] [--all] [--at 2026-09-28T13:54]` | この先 5 分間の時報 (スケジューラ用) |
| `demo [--sound-only] [--at ...]` | 次の正時の時報を間を詰めて今すぐ鳴らす |
| `simulate [--hours N] [--all]` | この先の時報を一覧表示 |
| `events [--hours N] [--refresh]` | 予定一覧 |
| `countdown TIME [--offsets ...] / --list / --cancel ID` | カウントダウン |
| `say TEXT` | 音声確認 |

- 設定: `~/.config/hourly-chime/config.yaml` (書式は `config.example.yaml`)
- 秘密: `~/.config/hourly-chime/secrets.yaml` (書式は `secrets.example.yaml`、chmod 600)
- ログ: `~/.local/state/hourly-chime/chime.log`
- キャッシュ: `~/.cache/hourly-chime/` (ICS・合成済み wav)
