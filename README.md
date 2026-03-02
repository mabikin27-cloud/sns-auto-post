# SNS投稿案自動生成ツール（LINE + Flask）

LINE で「ネタ」を送ると、Gemini が Instagram / LINE / ブログ用の投稿文を生成し、スプレッドシートに記録します。生成した 3 形式は LINE にも送信されます。

## 必要なもの

- LINE 公式アカウント（Messaging API 有効）
- Google スプレッドシート（サービスアカウントで共有）
- Gemini API キー

## セットアップ

### 1. 環境変数

`.env.example` をコピーして `.env` を作成し、値を設定してください。

```bash
cp .env.example .env
```

| 変数名 | 説明 |
|--------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE チャネルアクセストークン |
| `LINE_CHANNEL_SECRET` | LINE チャネルシークレット |
| `GEMINI_API_KEY` | Google AI Studio の API キー |
| `SPREADSHEET_ID` | スプレッドシートの ID（URL の `/d/xxxxx/` の部分） |
| `GOOGLE_APPLICATION_CREDENTIALS` | サービスアカウント JSON のパス（例: `./service_account.json`） |

Render などでデプロイする場合は、環境変数に `GOOGLE_CREDENTIALS_JSON` にサービスアカウントの JSON を**1行で**設定すると、ファイルのアップロードなしで利用できます。

### 2. スプレッドシートの準備

- **シート名「投稿管理」**  
  列: `日時` / `ネタ` / `ステータス` / `Instagram用` / `LINE用` / `ブログ用`（1行目はヘッダー推奨）

- **シート名「設定」**  
  A列＝項目名、B列＝内容（**B列は変更可能**）。1行目はヘッダー（例: A1 は空、B1 は「内容」）、2行目以降がデータ。  
  例: A2「専門家の役割」B2「元看護師の分子栄養カウンセラー」、A3「発信スタイル」、A4「共通禁止事項」、A5「ペルソナ」など。  
  すべての行が「項目: 内容」として Gemini のプロンプトに渡され、生成のトーンや禁止事項に反映されます。

スプレッドシートは、`service_account.json` の `client_email` に**編集者**で共有してください。

### 3. ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

`http://localhost:5000` で起動します。LINE の Webhook URL には ngrok 等で公開した URL を設定してください（例: `https://xxxx.ngrok.io/webhook`）。

### 4. Render にデプロイする場合

- **Web Service** で、リポジトリを接続するかマニュアルデプロイでコードをアップロード。
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn app:app`（`gunicorn` は `requirements.txt` に追加してください）
- **Environment**: 上記の環境変数をすべて設定。`GOOGLE_CREDENTIALS_JSON` を使う場合は、JSON を 1 行にした文字列をそのまま貼り付け。

LINE の Webhook URL に `https://あなたのサービス名.onrender.com/webhook` を指定し、Webhook を有効にしてください。

## 動作フロー

1. ユーザーが LINE でテキスト（ネタ）を送信
2. すぐに「生成を開始します。30秒ほどお待ちください...」と返信
3. スプレッドシート「投稿管理」にネタを追加 → 「設定」からペルソナ取得 → Gemini で 3 形式生成 → 同じ行を「生成済」で更新
4. 完了したら LINE に「生成が完了しました。」のあと、**【Instagram用】【LINE用】【ブログ用】** の 3 通を Push。エラー時はエラー内容を 1 通 Push。

設定シートが読めない場合は「設定シートを確認してください。」と LINE で通知します。

## 429 エラー（レート制限）・404 エラー（モデルなし）

「現在の割り当てを超えました」と出る場合は、Gemini API の無料枠の制限に達しています。約1分待ってから再度送信してください。429 のときは 1 回だけ 60 秒後に自動で再試行します。詳細は https://ai.google.dev/gemini-api/docs/rate-limits を参照してください。

- **リクエスト間隔**: 前回から 7 秒経過するまで待ってから Gemini を呼び出します。`GEMINI_MIN_INTERVAL_SECONDS` で変更可（例: 10 で約 6 RPM）。
- **429 時**: 最大 3 回まで再試行。エラーに「○秒後に再試行」とあればその秒数で待機します。
- **404 時（モデルが見つからない）**: デフォルトで `gemini-2.5-flash` → `gemini-2.5-flash-lite` → `gemini-2.0-flash` の順にフォールバックします。環境変数 `GEMINI_MODEL_FALLBACK` にカンマ区切りでモデル名を指定するとその順で試します（例: `gemini-2.5-flash-lite,gemini-2.5-flash`）。
- **堂々巡りを避けるには**: 無料枠だけでは 429 が続く場合があります。[Google AI Studio](https://aistudio.google.com/) で**課金を有効**にすると割り当てが増え、安定して利用できます。
