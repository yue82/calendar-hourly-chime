# calendar-hourly-chime

Google カレンダー (iCal) と連動する時報・予定通知・カウントダウン。WSL2 + Windows 用。

タイミング・音・文面・優先順は全て設定 (`config.yaml`) で決める。以下は既定の設定。

### 時報 (`hour_chime`)

| | いつ | 内容 |
|---|---|---|
| 平日 | `weekday_hours` の正時 (8〜20時) | 5分前にポポポポポ (`weekday_pre_cues`)、正時にピピピポーン「N時です。」+ N:01〜次の時報ちょうどに始まる予定 |
| 休日 | `holiday_hours` の正時 (8・12・16・20時) | 正時のみ |

- 休日 = `holiday.weekdays` の曜日、または `holiday.all_day` の条件に合う終日予定がある日 (祝日・有休など)
- 「次の時報」は実際に次に鳴る時報 (20時の次は翌朝8時)
- 予定名の無い予定 (`busy_only` の枠など) の時間内に収まる、予定名の分かる予定があれば、そちらを読む

### 予定通知 (`event_notice`、全予定、平日・休日・夜間とも)

| タイミング | 通常 | 他の予定中 (`when_busy: sound_only`) |
|---|---|---|
| 5分前 | ポポポポポ「H時M分から、〇〇です。」 | ポポポポポ |
| 2分前 | ポポ | ポポ |
| 20秒前 | ポーン「〇〇です。」(予定名が無ければ音だけ) | ポーン |
| 開始時 | ピポーン (言葉なし) | ピポーン |

開始時・予定中に情報を読まないのは、Web 会議などに音声を聞かせないため。

### カウントダウン (`countdown`、夜間・休日も)

| タイミング | 音 | 予定名あり | 予定名なし |
|---|---|---|---|
| 30分前 | ピン | 「H時M分の〇〇まで、あと30分です。」 | 「H時M分まで、あと30分です。」 |
| 20分前 | ピン | 「〇〇まで、あと20分です。」 | 「あと20分です。」 |
| 10分前 | ピンピン | 「H時M分の〇〇まで、あと10分です。」 | 「H時M分まで、あと10分です。」 |
| 5分前 | ピンピン | 「〇〇まで、あと5分です。」 | 「あと5分です。」 |
| 2分前・1分前 | ピンピンピン | 「〇〇まで、あとN分です。」 | 「あとN分です。」 |
| 時刻 | ピポーン | 「H時M分、〇〇の時間です。」 | 「H時M分です。」 |

- 他の予定中は音だけ
- コマンド: `calendar-hourly-chime countdown 15:30 --label 出発` (`1530`、`+45` = 45 分後、`--offsets 30,10,5`)。
  一覧 `--list`、取り消し `--cancel ID|all`
- カレンダー: `countdown: true` を付けたカレンダーの予定 (予定名 = タイトル)。時報・予定通知・予定中の判定には一切使わない
- Google カレンダーの「タスク」は API で時刻が取れない (日付のみ) ので使えない

### 応答不可 (`do_not_disturb`)

予定の 30 秒前から終了の 30 秒後まで、Windows の応答不可をオンにする (続く予定の間は途切れない)。
このプログラムがオンにしたものだけ予定後にオフに戻し、予定中に手動でオフにしたらその予定の間はオンにしない。
その日最初の確認で予定中でなければ、必ずオフにする (`first_run_off`)。
公開 API が無いので、状態は非公開の WNF で読み、切り替えは UI Automation で通知センターを開いて行う (一瞬開く)。
`calendar-hourly-chime dnd status|on|off` で確認・手動切り替え。

### 共通

- 「予定中」= その時刻に終日以外の予定が入っている (カウントダウン専用カレンダーと、Google カレンダーで「予定なし」にした予定を除く)。
  「予定なし」の予定も、時報の案内・予定通知では読み上げる
- 予定中の時報 (5分前を含む) は音だけ・音量 20% (`hour_chime.busy_volume`)。予定通知・カウントダウンにも `busy_volume` がある (既定 1.0)
- `conflict_seconds` (10 秒) 以内に重なったら `priority` の順に残す (既定: カウントダウン > 予定通知 > 時報)
- 音は組み込み (pipipipoon pipoon popopopopo popo poon pin pinpin pinpinpin) か、`sounds` で wav に差し替え

```
タスクスケジューラ (5 分ごと x4:00/x9:00) → conhost --headless wsl.exe → calendar-hourly-chime run
  → ICS 取得 (キャッシュ) → 起動 30 秒後からの 5 分間の計画 → TTS + 音を wav に合成
  → PowerShell 1 プロセスが Windows の時計で各時刻まで待って再生
```

## セットアップ

```sh
uv sync
mkdir -p ~/.config/calendar-hourly-chime
cp config.example.yaml ~/.config/calendar-hourly-chime/config.yaml   # 普段の設定
install -m 600 secrets.example.yaml ~/.config/calendar-hourly-chime/secrets.yaml  # 非公開 URL・キー
uv run calendar-hourly-chime demo                 # 時報・予定通知・カウントダウンを試聴
uv run calendar-hourly-chime simulate --hours 24  # この先の予定を確認
powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w scripts/install_task.ps1)"
```

WSL で systemd を使っていると、起動後に interop (WSL から `.exe` を起動する機能) の登録が見えなくなり
`powershell.exe` が `exec format error` になることがある。起動のたびに登録し直すサービスを入れておく:

```sh
sudo cp scripts/wsl-interop.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now wsl-interop.service
```

削除は `install_task.ps1 -Uninstall`。

空き時間情報しか共有されていないカレンダー (会社アカウント等) は、共有先のアカウントで
`gas/busy_ics.gs` を Apps Script のウェブアプリとしてデプロイし、その URL を secrets.yaml に書いて `busy_only: true` で登録する
(手順はファイル先頭のコメント)。

## コマンド

| コマンド | 内容 |
|---|---|
| `run [--dry-run] [--all] [--at 2026-09-28T13:54]` | この先 5 分間の分を鳴らす (スケジューラ用) |
| `demo [--sound-only] [--sounds]` | 時報・予定通知・カウントダウンを間を詰めて今すぐ鳴らす (`--sounds`: 使っている音の一覧) |
| `simulate [--hours N] [--all]` | この先の分を一覧表示 |
| `events [--hours N] [--refresh]` | 予定一覧 |
| `countdown TIME [--label ...] [--offsets ...] / --list / --cancel ID` | カウントダウン |
| `say TEXT` | 音声確認 |

- 設定: `~/.config/calendar-hourly-chime/config.yaml` (書式は `config.example.yaml`)
- 秘密: `~/.config/calendar-hourly-chime/secrets.yaml` (書式は `secrets.example.yaml`、chmod 600)
- ログ: `~/.local/state/calendar-hourly-chime/calendar-hourly-chime.log`
- キャッシュ: `~/.cache/calendar-hourly-chime/` (ICS・合成済み wav)
