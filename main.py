#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — 共有ドライブ フォルダ構造 一括抽出スクリプト
GitHub Actions (Python) で実行することを前提とした設計。

出力列:
  SharedDrive, Level1, Level2, Level3, Level4, Level5, Level6, URL, Permissions

設計方針:
  - GAS のタイムアウトを回避するため Python + Drive API v3 に完全移行
  - 再帰ではなくキューベース（BFS）でスタックオーバーフローを防止
  - 指数バックオフ付きリトライで 429 / 5xx を自動回復
  - ストリーミング CSV 書き込みでメモリ使用量を最小化
  - 既存ファイルの ID を維持したまま中身だけ上書き（Drive ファイルID固定）
  - subject（Workspace管理者）へのなりすましでドメイン全体の共有ドライブを取得
"""

import csv
import io
import json
import logging
import os
import sys
import time
from collections import deque

# ------------------------------------------------------------------ #
# 依存ライブラリ確認
# ------------------------------------------------------------------ #
try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload
except ImportError:
    print("エラー: google-api-python-client / google-auth が必要です。")
    print("  pip install google-api-python-client google-auth")
    sys.exit(1)

# ------------------------------------------------------------------ #
# 設定
# ------------------------------------------------------------------ #
# 出力先フォルダ ID（Google Drive）
OUTPUT_FOLDER_ID = os.environ.get(
    "OUTPUT_FOLDER_ID", "1ZqjDtUgYzueDU2_I0FJifiNPV1QFdnBF"
)
OUTPUT_FILE_NAME = "shared_drive_structure.csv"

# ドメイン全体の委任で使用するWorkspace管理者メールアドレス
# 環境変数 SUBJECT_EMAIL から取得（GitHub Secretsに登録）
SUBJECT_EMAIL = os.environ.get("SUBJECT_EMAIL", "")

# API スコープ
SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

# リトライ設定
MAX_RETRIES    = 6
RETRY_BASE_SEC = 2.0
RETRY_MAX_SEC  = 120.0

# API 呼び出し間隔（レート制限対策）
API_CALL_INTERVAL_SEC = 0.05

# フォルダ取得のページサイズ（Drive API v3 最大値）
PAGE_SIZE = 1000

# ------------------------------------------------------------------ #
# ロガー設定
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ================================================================== #
# 認証
# ================================================================== #
def build_drive_service():
    """
    サービスアカウント認証。
    SUBJECT_EMAIL を指定することでドメイン全体の委任（DWD）を使用し、
    Workspace管理者としてすべての共有ドライブにアクセスする。
    """
    if not SUBJECT_EMAIL:
        log.warning(
            "SUBJECT_EMAIL が設定されていません。"
            "共有ドライブが0件になる可能性があります。"
            "GitHub Secrets に SUBJECT_EMAIL（Workspace管理者メール）を追加してください。"
        )

    cred_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if cred_json:
        info = json.loads(cred_json)
        creds = Credentials.from_service_account_info(
            info,
            scopes=SCOPES,
            subject=SUBJECT_EMAIL if SUBJECT_EMAIL else None,
        )
        log.info("認証: 環境変数 GOOGLE_CREDENTIALS_JSON を使用")
    else:
        cred_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        if not os.path.exists(cred_file):
            raise FileNotFoundError(
                f"認証ファイルが見つかりません: {cred_file}\n"
                "環境変数 GOOGLE_CREDENTIALS_JSON または GOOGLE_APPLICATION_CREDENTIALS を設定してください。"
            )
        creds = Credentials.from_service_account_file(
            cred_file,
            scopes=SCOPES,
            subject=SUBJECT_EMAIL if SUBJECT_EMAIL else None,
        )
        log.info(f"認証: ファイル {cred_file} を使用")

    if SUBJECT_EMAIL:
        log.info(f"委任ユーザー: {SUBJECT_EMAIL}")

    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ================================================================== #
# リトライ付き API 呼び出し
# ================================================================== #
def api_call_with_retry(func, *args, **kwargs):
    """
    指数バックオフ付きリトライで Drive API を呼び出す。
    429 / 5xx を自動回復。
    """
    import random
    wait = RETRY_BASE_SEC
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(API_CALL_INTERVAL_SEC)
            return func(*args, **kwargs).execute()
        except HttpError as e:
            status = e.resp.status
            if status in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                jitter    = wait * 0.2 * (0.5 - random.random())
                sleep_sec = min(wait + jitter, RETRY_MAX_SEC)
                log.warning(
                    f"HTTP {status} — {attempt}/{MAX_RETRIES} 回目リトライ。"
                    f"{sleep_sec:.1f}秒待機..."
                )
                time.sleep(sleep_sec)
                wait = min(wait * 2, RETRY_MAX_SEC)
            else:
                raise
    raise RuntimeError("最大リトライ回数に達しました")


# ================================================================== #
# 共有ドライブ一覧取得
# ================================================================== #
def list_shared_drives(service):
    """
    認証ユーザー（SUBJECT_EMAIL）が参加している共有ドライブだけ取得。
    管理者権限は不要。
    """
    drives     = []
    page_token = None

    while True:
        params = {
            "pageSize": 100,
            "fields":   "nextPageToken, drives(id, name)",
        }
        if page_token:
            params["pageToken"] = page_token

        resp       = api_call_with_retry(service.drives().list, **params)
        items      = resp.get("drives", [])
        drives.extend(items)
        page_token = resp.get("nextPageToken")
        log.info(f"  共有ドライブ取得中... 累計 {len(drives)} 件")
        if not page_token:
            break

    log.info(f"共有ドライブ総数: {len(drives)} 件（{SUBJECT_EMAIL} がアクセス可能なもの）")
    return drives


# ================================================================== #
# フォルダ権限取得
# ================================================================== #
def get_permissions(service, folder_id):
    """
    フォルダの権限一覧を "email(role), ..." 形式の文字列で返す。
    管理者権限なしで通常取得のみ。
    """
    try:
        resp = api_call_with_retry(
            service.permissions().list,
            fileId=folder_id,
            fields="permissions(emailAddress,displayName,role,type)",
            supportsAllDrives=True,
        )
        perms = resp.get("permissions", [])
        parts = []
        for p in perms:
            identity = p.get("emailAddress") or p.get("displayName") or p.get("type", "")
            role     = p.get("role", "")
            if identity:
                parts.append(f"{identity}({role})")
        return ", ".join(parts)
    except HttpError as e:
        log.warning(f"権限取得失敗 [{folder_id}]: {e}")
        return "権限取得不可"

# ================================================================== #
# 子フォルダ一覧取得
# ================================================================== #
def list_child_folders(service, parent_id, drive_id):
    """指定フォルダの直下にある子フォルダを全件取得"""
    folders    = []
    page_token = None
    query      = (
        f"'{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )

    while True:
        params = {
            "q":                         query,
            "pageSize":                  PAGE_SIZE,
            "fields":                    "nextPageToken, files(id, name, webViewLink)",
            "supportsAllDrives":         True,
            "includeItemsFromAllDrives": True,
            "corpora":                   "drive",
            "driveId":                   drive_id,
        }
        if page_token:
            params["pageToken"] = page_token

        resp       = api_call_with_retry(service.files().list, **params)
        items      = resp.get("files", [])
        folders.extend(items)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return folders


# ================================================================== #
# BFS（幅優先探索）でドライブ全体をクロール
# ================================================================== #
def crawl_drive(service, drive, csv_writer, stats):
    """
    1つの共有ドライブをキューベースBFSで探索し、
    各フォルダをCSVに逐次書き込む。
    """
    drive_name = drive["name"]
    drive_id   = drive["id"]
    log.info(f"  ▶ クロール開始: {drive_name} ({drive_id})")

    # キュー要素: (folder_id, url, path_list)
    # path_list は Level1〜Level6 の値を持つリスト
    queue = deque()
    queue.append((drive_id, f"https://drive.google.com/drive/folders/{drive_id}", []))

    drive_folder_count = 0

    while queue:
        folder_id, folder_url, path = queue.popleft()
        depth = len(path)

        # 権限取得
        try:
            permissions = get_permissions(service, folder_id)
        except Exception as e:
            log.warning(f"    権限取得エラー [{folder_id}]: {e}")
            permissions = "エラー"
            stats["errors"] += 1

        # CSV 1行分を構築（Level1〜Level6 を常に6要素に揃える）
        levels = (path + [""] * 6)[:6]
        row    = [drive_name] + levels + [folder_url, permissions]
        csv_writer.writerow(row)

        drive_folder_count += 1
        stats["folders"]   += 1

        if stats["folders"] % 500 == 0:
            log.info(f"    進捗: 累計 {stats['folders']} フォルダ処理済み")

        # 最大深度（Level6）を超えたら子フォルダは取得しない
        if depth >= 6:
            continue

        # 子フォルダを取得してキューに追加
        try:
            children = list_child_folders(service, folder_id, drive_id)
        except Exception as e:
            log.warning(f"    子フォルダ取得エラー [{folder_id}]: {e}")
            stats["errors"] += 1
            continue

        for child in children:
            child_path = path + [child["name"]]
            child_url  = child.get("webViewLink", "")
            queue.append((child["id"], child_url, child_path))

    log.info(f"  ✓ {drive_name}: {drive_folder_count} フォルダ")
    return drive_folder_count


# ================================================================== #
# Google Drive へ CSV をアップロード / 上書き
# ================================================================== #
def find_existing_file(service, folder_id, file_name):
    """
    指定フォルダ内から file_name に一致するファイルを検索してIDを返す。
    見つからなければ None。
    """
    query = (
        f"'{folder_id}' in parents "
        f"and name='{file_name}' "
        f"and trashed=false"
    )
    resp  = api_call_with_retry(
        service.files().list,
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    )
    files = resp.get("files", [])
    if files:
        found = files[0]
        log.info(f"既存ファイルを発見: {found['name']} (ID: {found['id']})")
        return found["id"]
    return None


def upload_csv_to_drive(service, csv_bytes, folder_id, file_name):
    """
    CSVデータをGoogle Driveにアップロード。
    既存ファイルがあれば中身だけ上書き（ID維持）。
    なければ新規作成。
    """
    media = MediaIoBaseUpload(
        io.BytesIO(csv_bytes),
        mimetype="text/csv",
        resumable=True,
    )

    existing_id = find_existing_file(service, folder_id, file_name)

    if existing_id:
        log.info(f"既存ファイルを上書き中... (ID: {existing_id})")
        api_call_with_retry(
            service.files().update,
            fileId=existing_id,
            media_body=media,
            supportsAllDrives=True,
        )
        log.info(f"上書き完了: {file_name} (ID: {existing_id})")
        return existing_id
    else:
        log.info(f"新規ファイルを作成中: {file_name}")
        file_metadata = {
            "name":    file_name,
            "parents": [folder_id],
        }
        resp = api_call_with_retry(
            service.files().create,
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )
        new_id = resp["id"]
        log.info(f"新規作成完了: {file_name} (ID: {new_id})")
        return new_id


# ================================================================== #
# メイン処理
# ================================================================== #
def main():
    start_time = time.time()
    log.info("=" * 60)
    log.info(" 共有ドライブ フォルダ構造 一括抽出スクリプト")
    log.info("=" * 60)

    # ── 認証 ──
    log.info("\n[Step 1] Google Drive API に接続...")
    service = build_drive_service()

    # ── 共有ドライブ一覧取得 ──
    log.info("\n[Step 2] 共有ドライブ一覧を取得...")
    drives = list_shared_drives(service)
    if not drives:
        log.warning("共有ドライブが 0 件でした。")
        log.warning("以下を確認してください:")
        log.warning("  1. SUBJECT_EMAIL に Workspace管理者のメールアドレスが設定されているか")
        log.warning("  2. 管理コンソールでドメイン全体の委任が設定されているか")
        log.warning("  3. スコープ https://www.googleapis.com/auth/drive が含まれているか")
        sys.exit(1)

    # ── CSV をメモリ上に構築しながら各ドライブをクロール ──
    log.info(f"\n[Step 3] 全 {len(drives)} 件のドライブをクロール中...")

    csv_buffer = io.StringIO()
    writer     = csv.writer(csv_buffer, lineterminator="\n")

    # ヘッダ行
    writer.writerow([
        "SharedDrive",
        "Level1", "Level2", "Level3", "Level4", "Level5", "Level6",
        "URL", "Permissions",
    ])

    stats = {"folders": 0, "errors": 0}

    for i, drive in enumerate(drives, 1):
        log.info(f"\n  [{i}/{len(drives)}] {drive['name']}")
        try:
            crawl_drive(service, drive, writer, stats)
        except Exception as e:
            log.error(f"  ドライブクロール失敗 [{drive['name']}]: {e}")
            stats["errors"] += 1

    log.info(f"\n  クロール完了: {stats['folders']} フォルダ / エラー {stats['errors']} 件")

    # ── ローカルにも保存（Actions の artifact 用） ──
    csv_content_str = csv_buffer.getvalue()
    with open(OUTPUT_FILE_NAME, "w", encoding="utf-8-sig", newline="") as f:
        f.write(csv_content_str)
    log.info(f"\n  ローカル保存: {OUTPUT_FILE_NAME} ({len(csv_content_str):,} 文字)")

    # ── Google Drive にアップロード ──
    log.info(f"\n[Step 4] Google Drive にアップロード (フォルダID: {OUTPUT_FOLDER_ID})...")
    csv_bytes = csv_content_str.encode("utf-8-sig")
    file_id   = upload_csv_to_drive(service, csv_bytes, OUTPUT_FOLDER_ID, OUTPUT_FILE_NAME)

    # ── 完了サマリー ──
    elapsed = time.time() - start_time
    log.info("\n" + "=" * 60)
    log.info(" 完了サマリー")
    log.info("=" * 60)
    log.info(f"  共有ドライブ数  : {len(drives)}")
    log.info(f"  総フォルダ数    : {stats['folders']}")
    log.info(f"  エラー件数      : {stats['errors']}")
    log.info(f"  CSVサイズ       : {len(csv_bytes) / 1024 / 1024:.2f} MB")
    log.info(f"  Drive ファイルID: {file_id}")
    log.info(f"  所要時間        : {elapsed / 60:.1f} 分")
    log.info("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"\n致命的エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
