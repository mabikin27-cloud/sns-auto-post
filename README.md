# SNS投稿案自動生成ツール（LINE + Flask）

LINE で「ネタ」を送ると、Gemini が Instagram / LINE / ブログ用の投稿文を生成し、スプレッドシートに記録してから LINE で返信します。

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
  ペルソナを次のいずれかで記載  
  - 列名に「ペルソナ」を含む列を 1 つ用意し、2 行目以降にペルソナ文を書く  
  - または A 列に `ペルソナ`、B 列にその内容を書く  

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
4. 生成結果を LINE の Push で 3 通（Instagram用 / LINE用 / ブログ用）送信

設定シートが読めない場合は「設定シートを確認してください。」と LINE で通知します。
