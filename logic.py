"""
SNS投稿案生成ロジック: Gemini API と Google スプレッドシート（gspread）の操作
"""
import os
import re
import json
import time
import traceback
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types


# スコープ
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Gemini レート制限対策: 前回リクエストからの最小間隔（秒）。環境変数 GEMINI_MIN_INTERVAL_SECONDS で変更可。
_gemini_last_request_time = 0.0
GEMINI_MIN_INTERVAL_DEFAULT = 7

# 設定シート読み込みエラー用
class SettingsSheetError(Exception):
    """設定シートの読み込みに失敗したときに使用"""
    pass


def _wait_gemini_rate_limit():
    """Gemini のレート制限を避けるため、前回リクエストから一定時間経過するまで待つ。"""
    global _gemini_last_request_time
    interval = float(os.environ.get("GEMINI_MIN_INTERVAL_SECONDS", GEMINI_MIN_INTERVAL_DEFAULT))
    interval = max(1.0, min(interval, 120.0))
    elapsed = time.monotonic() - _gemini_last_request_time
    if elapsed < interval:
        wait = interval - elapsed
        print(f"[INFO] レート制限対策: {wait:.1f}秒待機します...")
        time.sleep(wait)
    _gemini_last_request_time = time.monotonic()


def _parse_429_retry_seconds(error_message: str):
    """429 エラーメッセージから再試行までの秒数を取り出す。"""
    m = re.search(r"(\d+)(?:\.\d+)?\s*秒", error_message)
    if m:
        return min(120, max(10, int(float(m.group(1)))))
    return None


def _get_credentials():
    """環境変数からサービスアカウント認証を取得（GOOGLE_CREDENTIALS_JSON / GCP_SERVICE_ACCOUNT_JSON またはファイル）"""
    json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if json_str:
        try:
            info = json.loads(json_str)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return creds
        except json.JSONDecodeError as e:
            print(f"[ERROR] 認証 JSON の解析に失敗（GOOGLE_CREDENTIALS_JSON / GCP_SERVICE_ACCOUNT_JSON）: {e}")
            raise ValueError("GOOGLE_CREDENTIALS_JSON または GCP_SERVICE_ACCOUNT_JSON の JSON 形式が不正です") from e
        except Exception as e:
            print(f"[ERROR] 認証の読み込みに失敗: {e}")
            raise
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
    if not os.path.isfile(path):
        print(f"[ERROR] 認証ファイルが見つかりません: {path}")
        raise FileNotFoundError(f"認証ファイルが見つかりません: {path}")
    try:
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
        return creds
    except Exception as e:
        print(f"[ERROR] 認証ファイルの読み込みに失敗: {e}")
        raise


def _get_persona(ws_settings):
    """「設定」シートからペルソナ情報を取得する"""
    try:
        all_values = ws_settings.get_all_values()
    except Exception as e:
        print(f"[ERROR] 設定シートの取得に失敗: {e}")
        raise SettingsSheetError("設定シートを読み込めません") from e

    if not all_values:
        print("[ERROR] 設定シートが空です")
        raise SettingsSheetError("設定シートを確認してください")

    # 1行目をヘッダーとして扱い、「ペルソナ」列を探す
    headers = [str(h).strip() for h in all_values[0]]
    persona_col = None
    for i, h in enumerate(headers):
        if "ペルソナ" in h or h == "persona":
            persona_col = i
            break

    if persona_col is not None and len(all_values) > 1:
        # 2行目以降のペルソナを結合して使用（複数行対応）
        persona_parts = []
        for row in all_values[1:]:
            if len(row) > persona_col and row[persona_col].strip():
                persona_parts.append(row[persona_col].strip())
        if persona_parts:
            return "\n".join(persona_parts)

    # ヘッダーに「ペルソナ」が無い場合は A 列キー・B 列値形式を想定
    for row in all_values[1:]:
        if len(row) >= 2 and str(row[0]).strip() == "ペルソナ":
            return str(row[1]).strip()

    # デフォルト: 空で続行（ペルソナなしで生成）
    return ""


def generate_posts(neta: str) -> dict:
    """
    ネタを受け取り、スプレッドシートへの記録・Gemini による 3 形式生成・シート更新まで行い、
    Instagram用 / LINE用 / ブログ用 のテキストを返す。

    Returns:
        {"instagram": str, "line": str, "blog": str}

    Raises:
        SettingsSheetError: 設定シートが読めない場合（LINE で「設定シートを確認してください」と通知する想定）
    """
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID が設定されていません")

    print("[1/5] スプレッドシートに接続しています...")
    creds = _get_credentials()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)

    # 投稿管理シートにネタを追加
    try:
        ws_posts = sh.worksheet("投稿管理")
    except gspread.WorksheetNotFound:
        print("[ERROR] 「投稿管理」シートが見つかりません")
        raise SettingsSheetError("設定シートを確認してください")

    print("[2/5] 投稿管理シートにネタを追加しています...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 列: 日時, ネタ, ステータス, Instagram用, LINE用, ブログ用 を想定（既存行がある場合はヘッダーあり）
    row_data = [now, neta, "処理中", "", "", ""]
    ws_posts.append_row(row_data, value_input_option="USER_ENTERED")
    # 追加した行のインデックス（1-based）
    all_rows = ws_posts.get_all_values()
    added_row_index = len(all_rows)

    # 設定シートからペルソナ取得
    print("[3/5] 設定シートからペルソナを取得しています...")
    try:
        ws_settings = sh.worksheet("設定")
    except gspread.WorksheetNotFound:
        print("[ERROR] 「設定」シートが見つかりません")
        ws_posts.update_cell(added_row_index, 3, "エラー: 設定シートなし")
        raise SettingsSheetError("設定シートを確認してください")

    persona = _get_persona(ws_settings)

    # Gemini で 3 形式生成
    print("[4/5] Gemini で投稿案を生成しています...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    # モデル名と API バージョン（環境変数で上書き可能）
    model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    api_version = os.environ.get("GEMINI_API_VERSION", "v1")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version=api_version),
    )

    prompt = f"""以下はSNS・ブログ用の投稿ネタです。このネタをもとに、次の3種類の投稿文を生成してください。

【ネタ】
{neta}

【ペルソナ（ターゲット）】
{persona if persona else "指定なし"}

【出力形式】
以下の3つを、必ず「## Instagram」」「## LINE」「## Blog」の見出しで区切って出力してください。
- Instagram用: 短文・ハッシュタグ含む・インスタ向けトーン
- LINE用: 友だち向けのカジュアルな短文（改行可）
- ブログ用: やや長めの読みやすい文章（2〜3文程度）

見出し以外に余計な説明は書かず、各見出しの直後に投稿文のみを書いてください。"""

    response = None
    last_error = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            _wait_gemini_rate_limit()
            response = client.models.generate_content(model=model_name, contents=prompt)
            break
        except Exception as e:
            last_error = e
            err_str = str(e)
            err_lower = err_str.lower()
            is_429 = "429" in err_lower or "resourceexhausted" in type(e).__name__.lower()
            if is_429 and attempt < max_attempts - 1:
                wait_sec = _parse_429_retry_seconds(err_str) or (60 + attempt * 30)
                wait_sec = min(120, wait_sec)
                print(f"[WARN] Gemini レート制限(429)。{wait_sec}秒後に再試行します（{attempt + 1}/{max_attempts}）...")
                time.sleep(wait_sec)
                continue
            print(f"[ERROR] Gemini 生成エラー: {e}")
            print(traceback.format_exc())
            ws_posts.update_cell(added_row_index, 3, "エラー: 生成失敗")
            raise
    if response is None:
        raise last_error or ValueError("Gemini から応答がありませんでした")

    try:
        # ブロック・空応答の場合は .text が取得できない／例外になることがある
        try:
            text = (response.text or "").strip()
        except (ValueError, AttributeError) as e:
            print(f"[ERROR] Gemini 応答テキスト取得失敗: {e}")
            reason = "応答を取得できませんでした"
            if getattr(response, "prompt_feedback", None):
                reason = str(response.prompt_feedback)
            candidates = getattr(response, "candidates", None)
            if candidates and len(candidates) > 0:
                c = candidates[0]
                if getattr(c, "finish_reason", None):
                    reason = str(c.finish_reason)
            ws_posts.update_cell(added_row_index, 3, "エラー: 生成失敗")
            raise ValueError(f"Gemini: {reason}") from e
        if not text:
            print("[ERROR] Gemini の応答テキストが空です")
            ws_posts.update_cell(added_row_index, 3, "エラー: 生成失敗")
            raise ValueError("Gemini の応答が空でした")
    except ValueError:
        raise
    except Exception as e:
        print(f"[ERROR] Gemini 生成エラー: {e}")
        print(traceback.format_exc())
        ws_posts.update_cell(added_row_index, 3, "エラー: 生成失敗")
        raise

    # パース: ## Instagram / ## LINE / ## Blog で分割
    instagram_text = ""
    line_text = ""
    blog_text = ""
    current = None
    buf = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            if current:
                buf.append(raw_line)
            continue
        if "## Instagram" in line or "##instagram" in line.lower():
            if current == "line":
                line_text = "\n".join(buf).strip()
            elif current == "blog":
                blog_text = "\n".join(buf).strip()
            current = "instagram"
            buf = []
        elif "## LINE" in line or "##line" in line.lower():
            if current == "instagram":
                instagram_text = "\n".join(buf).strip()
            elif current == "blog":
                blog_text = "\n".join(buf).strip()
            current = "line"
            buf = []
        elif "## Blog" in line or "##blog" in line.lower():
            if current == "instagram":
                instagram_text = "\n".join(buf).strip()
            elif current == "line":
                line_text = "\n".join(buf).strip()
            current = "blog"
            buf = []
        elif current:
            buf.append(raw_line)
    if current == "instagram":
        instagram_text = "\n".join(buf).strip()
    elif current == "line":
        line_text = "\n".join(buf).strip()
    elif current == "blog":
        blog_text = "\n".join(buf).strip()

    # パースに失敗した場合は全文を LINE 用に
    if not instagram_text and not line_text and not blog_text:
        line_text = text[:500]
        instagram_text = "（生成できませんでした）"
        blog_text = "（生成できませんでした）"
    if not instagram_text:
        instagram_text = line_text or "（未生成）"
    if not line_text:
        line_text = instagram_text or "（未生成）"
    if not blog_text:
        blog_text = line_text or "（未生成）"

    # 投稿管理シートの該当行を更新（ステータス・3形式）
    print("[5/5] 投稿管理シートを更新しています...")
    # 列: A=1 日時, B=2 ネタ, C=3 ステータス, D=4 Instagram, E=5 LINE, F=6 ブログ
    ws_posts.update_cell(added_row_index, 3, "生成済")
    ws_posts.update_cell(added_row_index, 4, instagram_text)
    ws_posts.update_cell(added_row_index, 5, line_text)
    ws_posts.update_cell(added_row_index, 6, blog_text)

    print("[完了] 生成済み。LINE に返信します。")
    return {
        "instagram": instagram_text,
        "line": line_text,
        "blog": blog_text,
    }
