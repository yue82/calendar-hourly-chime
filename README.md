# hourly-chime

Google カレンダー (iCal) と連動する音声時報。WSL2 + Windows 用。

```
タスクスケジューラ (毎時0分) → conhost --headless wsl.exe → hourly-chime chime
  → ICS 取得 (キャッシュ) → ルール判定 → TTS (SAPI / VOICEVOX) → PowerShell で再生
```

## セットアップ

```sh
uv sync
mkdir -p ~/.config/hourly-chime
cp config.example.yaml ~/.config/hourly-chime/config.yaml   # iCal URL などを編集
uv run hourly-chime say "テストです"                          # 音が出るか確認
uv run hourly-chime simulate --hours 24                      # この先の時報を確認
powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w scripts/install_task.ps1)"
```

削除は `install_task.ps1 -Uninstall`。

## コマンド

| コマンド | 内容 |
|---|---|
| `chime [--dry-run] [--force] [--at 2026-09-28T14:00]` | 時報 (正時±5分以外はスキップ) |
| `events [--hours N] [--refresh]` | 予定一覧 |
| `simulate [--hours N]` | この先の時報を一覧表示 |
| `say TEXT` | 音声確認 |

- 設定: `~/.config/hourly-chime/config.yaml` (書式は `config.example.yaml`)
- ログ: `~/.local/state/hourly-chime/chime.log`
- キャッシュ: `~/.cache/hourly-chime/` (ICS・合成済み wav)
