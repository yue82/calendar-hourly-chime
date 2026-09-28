# hourly-chime

Google カレンダー (iCal) と連動する音声時報。WSL2 + Windows 用。

### 時報

| | いつ | 内容 |
|---|---|---|
| 平日 | 毎正時 (`quiet_hours` を除く) | ピピピポーン「N時です。」+ N:01〜次の時報ちょうどまでに始まる予定 |
| 休日 | `holiday.hours` の正時 (`quiet_hours` より優先) | 同上 |

- 休日 = `holiday.weekdays` の曜日、または `holiday.all_day` の条件に合う終日予定がある日 (祝日・有休など)
- 「次の時報」は実際に次に鳴る時報 (20時の次は翌朝8時)。N:00 ちょうどの予定はその最中なので含めない
- 予定名の無い予定 (`busy_only` の枠など) の時間内に収まる、予定名の分かる予定があれば、そちらを読む

### 予定通知 (全予定、平日・休日・夜間とも)

| タイミング | 通常 | 他の予定中 |
|---|---|---|
| 5分前 | ポポポポポ「H時M分から、〇〇です。」 | ポポポポポ |
| 2分前 | ポポ | ポポ |
| 20秒前 | ポーン「〇〇です。」(予定名が無ければ音だけ) | ポーン |
| 開始時 | ピポーン (言葉なし) | ピポーン |

時報と 10 秒以内に重なった予定通知は鳴らさない (時報優先)。
開始時・予定中に情報を読まないのは、Web 会議などに音声を聞かせないため。

### カウントダウン (夜間・休日も)

| タイミング | 音 | 予定名あり | 予定名なし |
|---|---|---|---|
| 30分前 | ピン | 「H時M分の〇〇まで、あと30分です。」 | 「H時M分まで、あと30分です。」 |
| 20分前 | ピン | 「〇〇まで、あと20分です。」 | 「あと20分です。」 |
| 10分前 | ピンピン | 「H時M分の〇〇まで、あと10分です。」 | 「H時M分まで、あと10分です。」 |
| 5分前 | ピンピン | 「〇〇まで、あと5分です。」 | 「あと5分です。」 |
| 2分前・1分前 | ピンピンピン | 「〇〇まで、あとN分です。」 | 「あとN分です。」 |
| 時刻 | ピポーン | 「H時M分、〇〇の時間です。」 | 「H時M分です。」 |

- 他の予定中は音だけ。10 秒以内に重なる時報・予定通知は鳴らさない (カウントダウン優先)
- コマンド: `hourly-chime countdown 15:30 --label 出発` (`1530`、`+45` = 45 分後、`--offsets 30,10,5`)。
  一覧 `--list`、取り消し `--cancel ID|all`
- カレンダー: `countdown: true` を付けたカレンダーの予定 (予定名 = タイトル)。時報・予定通知には一切使わない
- Google カレンダーの「タスク」は API で時刻が取れない (日付のみ) ので使えない

### 共通

- 時報の時刻に予定 (終日予定を除く) が入っていれば、時報もピピピポーンだけ
- `busy_only` のカレンダーや名前の無い予定は、予定名の代わりに「予定があります」
- 案内は 1 回 `announce.max_items` 件まで。終日予定は案内・予定中の判定に使わない

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
uv run hourly-chime demo                                     # 時報・予定通知・カウントダウンを試聴
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
| `chime [--dry-run] [--all] [--at 2026-09-28T13:54]` | この先 5 分間の分を鳴らす (スケジューラ用) |
| `demo [--sound-only] [--sounds]` | 時報・予定通知・カウントダウンを間を詰めて今すぐ鳴らす (`--sounds`: 音の一覧) |
| `simulate [--hours N] [--all]` | この先の時報を一覧表示 |
| `events [--hours N] [--refresh]` | 予定一覧 |
| `countdown TIME [--label ...] [--offsets ...] / --list / --cancel ID` | カウントダウン |
| `say TEXT` | 音声確認 |

- 設定: `~/.config/hourly-chime/config.yaml` (書式は `config.example.yaml`)
- 秘密: `~/.config/hourly-chime/secrets.yaml` (書式は `secrets.example.yaml`、chmod 600)
- ログ: `~/.local/state/hourly-chime/chime.log`
- キャッシュ: `~/.cache/hourly-chime/` (ICS・合成済み wav)
