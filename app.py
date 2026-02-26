"""
LINE Webhook サーバー（Flask）
ユーザーが送った「ネタ」を受け取り、SNS投稿案を生成して返信する。
"""
import os

from flask import Flask, request, abort
from dotenv import load_dotenv

from logic import generate_posts, SettingsSheetError

load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")


def reply_line(reply_token: str, messages: list) -> bool:
    """LINE Reply API でメッセージを送信する。"""
    import requests
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": m} for m in messages],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=10)
    if resp.status_code != 200:
        print(f"[ERROR] LINE Reply API エラー: {resp.status_code} {resp.text}")
        return False
    return True


def validate_signature(body: bytes, signature: str) -> bool:
    """LINE Webhook の署名検証。"""
    import hmac
    import hashlib
    if not LINE_CHANNEL_SECRET:
        return False
    hash_val = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = hash_val.hex()
    return hmac.compare_digest(expected, signature)


def _push_message(user_id: str, text: str) -> bool:
    """LINE Push Message API でユーザーに送信。"""
    import requests
    if not user_id or not LINE_CHANNEL_ACCESS_TOKEN:
        return False
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=10)
    if resp.status_code != 200:
        print(f"[ERROR] LINE Push API エラー: {resp.status_code} {resp.text}")
        return False
    return True


@app.route("/", methods=["GET"])
def index():
    """ヘルスチェック用（Render の Live 確認など）。"""
    return "SNS投稿案生成 Bot は稼働中です。"


@app.route("/webhook", methods=["POST"])
def webhook():
    """LINE からの Webhook を受信する。"""
    if request.method != "POST":
        return "OK", 200

    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")

    if LINE_CHANNEL_SECRET and not validate_signature(body, signature):
        print("[ERROR] 署名検証に失敗しました")
        abort(403)

    payload = request.get_json(silent=True)
    if not payload or "events" not in payload:
        return "OK", 200

    for event in payload["events"]:
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        user_text = (msg.get("text") or "").strip()
        if not user_text:
            continue

        # 進捗表示: 受信直後に「生成を開始します。30秒ほどお待ちください...」を返信
        print("[受信] ネタを受信しました。即時返信を送ります。")
        reply_line(reply_token, ["生成を開始します。30秒ほどお待ちください..."])

        # 処理後に結果を Push で送信（Reply は 1 回までなので進捗で使用済み）
        try:
            print("[処理開始] 1/3 スプレッドシート・設定の読み込み")
            result = generate_posts(user_text)
            print("[処理完了] 3/3 生成完了")

            instagram = result.get("instagram", "")
            line_text = result.get("line", "")
            blog = result.get("blog", "")

            user_id = event.get("source", {}).get("userId")
            if user_id:
                _push_message(user_id, "【Instagram用】\n" + instagram)
                _push_message(user_id, "【LINE用】\n" + line_text)
                _push_message(user_id, "【ブログ用】\n" + blog)
            else:
                print("[WARN] userId が取得できません。結果を Push できません。")

        except SettingsSheetError as e:
            print(f"[ERROR] 設定シートエラー: {e}")
            _push_message(
                event.get("source", {}).get("userId") or "",
                "設定シートを確認してください。",
            )
        except Exception as e:
            print(f"[ERROR] 生成エラー: {e}")
            _push_message(
                event.get("source", {}).get("userId") or "",
                "投稿案の生成中にエラーが発生しました。しばらくしてからもう一度お試しください。",
            )

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
