/**
 * 共有されたカレンダーの「時間枠 (予定あり)」だけを ICS で返す Apps Script ウェブアプリ。
 * 空き時間情報 (free/busy) しか共有されていないカレンダーでも使える。
 *
 * セットアップ (カレンダーを共有されている側のアカウントで):
 *   1. https://script.google.com で新しいプロジェクトを作り、このファイルの中身を貼る
 *   2. CALENDAR_ID と KEY (推測されない長いランダム文字列) を書き換える
 *   3. 左の「サービス」+ → Google Calendar API を追加
 *   4. 関数 test を選んで実行 → 権限を許可 → ログに予定の件数が出ることを確認
 *   5. デプロイ → 新しいデプロイ → 種類: ウェブアプリ
 *        次のユーザーとして実行: 自分 / アクセスできるユーザー: 全員
 *   6. 出てきた URL の末尾に ?key=<KEY> を付けて config.yaml の url に書く
 * コードを変えたら「デプロイを管理」→ 編集 → バージョン: 新バージョン で更新する。
 */
const CALENDAR_ID = 'someone@example.com';  // 時間枠を取りたいカレンダーの ID (通常はメールアドレス)
const KEY = 'CHANGE-ME-to-a-long-random-string';
const DAYS_BACK = 1;
const DAYS_AHEAD = 14;

function doGet(e) {
  if (!e || !e.parameter || e.parameter.key !== KEY) {
    return ContentService.createTextOutput('forbidden');
  }
  return ContentService.createTextOutput(buildIcs_()).setMimeType(ContentService.MimeType.ICAL);
}

function buildIcs_() {
  const now = new Date();
  const res = Calendar.Freebusy.query({
    timeMin: new Date(now.getTime() - DAYS_BACK * 86400000).toISOString(),
    timeMax: new Date(now.getTime() + DAYS_AHEAD * 86400000).toISOString(),
    items: [{ id: CALENDAR_ID }],
  });
  const cal = res.calendars[CALENDAR_ID];
  if (cal.errors && cal.errors.length) {
    throw new Error(JSON.stringify(cal.errors));
  }
  const stamp = fmt_(now);
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//hourly-chime//busy//JA'];
  cal.busy.forEach(function (b) {
    lines.push(
      'BEGIN:VEVENT',
      'UID:' + fmt_(new Date(b.start)) + '-' + fmt_(new Date(b.end)) + '@hourly-chime',
      'DTSTAMP:' + stamp,
      'DTSTART:' + fmt_(new Date(b.start)),
      'DTEND:' + fmt_(new Date(b.end)),
      'SUMMARY:予定あり',
      'END:VEVENT'
    );
  });
  lines.push('END:VCALENDAR');
  return lines.join('\r\n') + '\r\n';
}

function fmt_(d) {
  return Utilities.formatDate(d, 'UTC', "yyyyMMdd'T'HHmmss'Z'");
}

function test() {
  const ics = buildIcs_();
  Logger.log('%s 件', (ics.match(/BEGIN:VEVENT/g) || []).length);
  Logger.log(ics.slice(0, 1000));
}
