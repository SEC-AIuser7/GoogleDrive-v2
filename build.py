#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 共有ドライブフォルダ構成データから data.js を生成するビルドスクリプト
CSV版（GAS出力CSVに対応）

データソース (build_config.json の source または環境変数 DATA_SOURCE):
  csv    : GASが出力したCSVファイル（Google Drive上 or ローカル）← デフォルト
  excel  : ローカルのExcelファイル
  sheets : Google Spreadsheet

CSVのヘッダ (GAS出力):
  SharedDrive,Level1,Level2,Level3,Level4,Level5,Level6,URL,Permissions

必要パッケージ:
  pip install pandas
  (csv + Drive API でダウンロードする場合) pip install google-api-python-client google-auth
  (sheets の場合) pip install gspread google-auth
  (excel の場合) pip install openpyxl
"""

import json
import os
import re
import sys
from datetime import datetime
from collections import defaultdict

try:
    import pandas as pd
except ImportError:
    print("エラー: pandas がインストールされていません。")
    print("    pip install pandas")
    sys.exit(1)

# ============================================================
# 設定
# ============================================================
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE    = os.path.join(SCRIPT_DIR, "共有ドライブフォルダ構成CSV.xlsx")
CONFIG_FILE   = os.path.join(SCRIPT_DIR, "build_config.json")
OUTPUT_FILE   = os.path.join(SCRIPT_DIR, "data.js")
SHEET_PREFIX  = "全共有ドライブ抽出_"

# レイアウト定数 (render.js と一致させること)
NODE_HEIGHT   = 22
NODE_GAP      = 8
ROW_STEP      = NODE_HEIGHT + NODE_GAP   # 30
LEVEL_X       = [20, 200, 420, 650, 880, 1100]
LEVEL_W       = [170, 200, 200, 200, 200, 200]
SVG_WIDTH     = 1340
TOP_MARGIN    = 30
BOTTOM_MARGIN = 30


# ============================================================
# 設定ファイル読み込み
# ============================================================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"source": "csv"}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("source", "csv")
        return cfg
    except Exception as e:
        print(f"  [警告] build_config.json 読み込み失敗: {e} → csv にフォールバック")
        return {"source": "csv"}


# ============================================================
# データ読み込み: CSV（GAS出力）
# ============================================================
def load_from_csv(cfg):
    """
    GASまたはmain.pyが出力したCSVを読み込む。

    優先順:
      1. 環境変数 CSV_FILE_PATH  (ローカルパス)
      2. build_config.json の csv_file_path
      3. 環境変数 CSV_DRIVE_FILE_ID → Drive APIでファイルIDから直接ダウンロード
      4. 環境変数 OUTPUT_FOLDER_ID → フォルダ内から shared_drive_structure.csv を検索
      5. build_config.json の csv_drive_file_id → Drive APIでダウンロード
      6. build_config.json の output_folder_id → フォルダ内から検索

    main.py 出力CSVのヘッダ:
      SharedDrive, Level1〜Level6, URL, Permissions
    """
    local_path = os.environ.get("CSV_FILE_PATH") or cfg.get("csv_file_path", "")
    drive_file_id = os.environ.get("CSV_DRIVE_FILE_ID") or cfg.get("csv_drive_file_id", "")
    output_folder_id = (
        os.environ.get("OUTPUT_FOLDER_ID")
        or cfg.get("output_folder_id", "")
        or "1ZqjDtUgYzueDU2_I0FJifiNPV1QFdnBF"
    )
    csv_file_name = cfg.get("csv_file_name", "shared_drive_structure.csv")

    if not local_path:
        if drive_file_id:
            # ファイルIDが直接指定されている場合
            local_path = _download_csv_from_drive(drive_file_id, cfg)
        elif output_folder_id:
            # フォルダから最新のCSVを検索
            print(f"  フォルダ内から '{csv_file_name}' を検索中...")
            file_id = _find_csv_in_folder(output_folder_id, csv_file_name, cfg)
            if file_id:
                print(f"  最新CSV発見: {file_id}")
                local_path = _download_csv_from_drive(file_id, cfg)

    if not local_path or not os.path.exists(local_path):
        raise FileNotFoundError(
            "CSVファイルが見つかりません。\n"
            "  build_config.json の csv_file_path / csv_drive_file_id / output_folder_id を設定するか、\n"
            "  環境変数 CSV_FILE_PATH / CSV_DRIVE_FILE_ID / OUTPUT_FOLDER_ID を指定してください。"
        )

    print(f"  ソース: CSV")
    print(f"  ファイル: {local_path}")
    print(f"  ファイルサイズ: {os.path.getsize(local_path):,} バイト")

    df = pd.read_csv(local_path, dtype=str, encoding="utf-8-sig")
    df = df.fillna("")

    # ★デバッグ出力（CSVの実態を必ず出す）
    print(f"  CSV 行数: {len(df)}")
    print(f"  CSV カラム: {list(df.columns)}")
    if len(df) > 0:
        print(f"  CSV 最初の3行（リネーム前）:")
        try:
            print(df.head(3).to_string(max_colwidth=40))
        except Exception:
            pass

    # 英語ヘッダ → 日本語ヘッダにリネーム（main.py出力対応）
    col_map = {
        "SharedDrive":  "共有ドライブ名",
        "Level1":       "階層1",
        "Level2":       "階層2",
        "Level3":       "階層3",
        "Level4":       "階層4",
        "Level5":       "階層5",
        "Level6":       "階層6",
        "URL":          "フォルダURL",
        "Permissions":  "全権限",
        # 別名揺れにも対応
        "shared_drive": "共有ドライブ名",
        "drive_name":   "共有ドライブ名",
        "ドライブ名":    "共有ドライブ名",
        "level1":       "階層1",
        "level2":       "階層2",
        "level3":       "階層3",
        "level4":       "階層4",
        "level5":       "階層5",
        "level6":       "階層6",
        "url":          "フォルダURL",
        "permissions":  "全権限",
        "全アクセス権限": "全権限",
        # 数字単独カラム（GAS旧形式、Sheets→CSVエクスポート時など）
        "1":            "階層1",
        "2":            "階層2",
        "3":            "階層3",
        "4":            "階層4",
        "5":            "階層5",
        "6":            "階層6",
        "権限":          "全権限",
    }
    rename_map = {k: v for k, v in col_map.items() if k in df.columns}
    if rename_map:
        print(f"  カラムリネーム: {rename_map}")
        df = df.rename(columns=rename_map)

    # GAS時代のパイプ区切り権限情報をスラッシュ区切りに統一
    if "全権限" in df.columns:
        df["全権限"] = df["全権限"].str.replace(r"\s*\|\s*", " / ", regex=True)

    print(f"  最終カラム: {list(df.columns)}")

    source_label = os.path.basename(local_path)
    return df, source_label


def _find_csv_in_folder(folder_id, file_name, cfg):
    """
    指定フォルダ内から file_name に一致するファイルのIDを返す。
    複数あれば最新のものを返す（modifiedTime で降順ソート）。
    見つからなければ None。
    """
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build as g_build
    except ImportError:
        print("エラー: google-api-python-client が必要です。")
        raise

    subject_email = os.environ.get("SUBJECT_EMAIL", "")
    cred_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    scopes = ["https://www.googleapis.com/auth/drive"]

    if cred_env:
        cred_info = json.loads(cred_env)
        creds = Credentials.from_service_account_info(
            cred_info, scopes=scopes,
            subject=subject_email if subject_email else None,
        )
    else:
        cred_file = cfg.get("credentials_file", "credentials.json")
        if not os.path.isabs(cred_file):
            cred_file = os.path.join(SCRIPT_DIR, cred_file)
        if not os.path.exists(cred_file):
            return None
        creds = Credentials.from_service_account_file(
            cred_file, scopes=scopes,
            subject=subject_email if subject_email else None,
        )

    service = g_build("drive", "v3", credentials=creds, cache_discovery=False)

    query = (
        f"'{folder_id}' in parents "
        f"and name='{file_name}' "
        f"and trashed=false"
    )
    resp = service.files().list(
        q=query,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    return None


def _download_csv_from_drive(file_id, cfg):
    """Google Drive API でCSVをダウンロードして一時ファイルパスを返す"""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build as g_build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        print("エラー: google-api-python-client が必要です。")
        print("    pip install google-api-python-client google-auth")
        raise

    # SUBJECT_EMAIL（ドメイン全体の委任で使うユーザー）
    subject_email = os.environ.get("SUBJECT_EMAIL", "")

    cred_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    scopes   = ["https://www.googleapis.com/auth/drive.readonly"]
    if cred_env:
        cred_info = json.loads(cred_env)
        creds = Credentials.from_service_account_info(
            cred_info, scopes=scopes,
            subject=subject_email if subject_email else None,
        )
    else:
        cred_file = cfg.get("credentials_file", "credentials.json")
        if not os.path.isabs(cred_file):
            cred_file = os.path.join(SCRIPT_DIR, cred_file)
        if not os.path.exists(cred_file):
            raise FileNotFoundError(
                f"認証ファイルが見つかりません: {cred_file}\n"
                "  GitHub Actions では環境変数 GOOGLE_CREDENTIALS_JSON を設定してください。"
            )
        creds = Credentials.from_service_account_file(
            cred_file, scopes=scopes,
            subject=subject_email if subject_email else None,
        )

    if subject_email:
        print(f"  委任ユーザー: {subject_email}")

    import io
    service  = g_build("drive", "v3", credentials=creds)
    request  = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf      = io.BytesIO()
    dl       = MediaIoBaseDownload(buf, request)
    done     = False
    while not done:
        _, done = dl.next_chunk()

    tmp_path = os.path.join(SCRIPT_DIR, "_downloaded_drive_data.csv")
    with open(tmp_path, "wb") as f:
        f.write(buf.getvalue())
    print(f"  Drive からダウンロード完了: {file_id} → {tmp_path}")
    return tmp_path


# ============================================================
# データ読み込み: Excel
# ============================================================
def load_from_excel(excel_path):
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"Excelファイルが見つかりません: {excel_path}")
    xls        = pd.ExcelFile(excel_path)
    candidates = sorted([s for s in xls.sheet_names if s.startswith(SHEET_PREFIX)], reverse=True)
    if not candidates:
        raise ValueError(f"シート '{SHEET_PREFIX}*' が見つかりません: {xls.sheet_names}")
    sheet_name = candidates[0]
    print(f"  ソース: Excel  ファイル: {os.path.basename(excel_path)}  シート: {sheet_name}")
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str).fillna("")
    return df, sheet_name


# ============================================================
# データ読み込み: Google Sheets
# ============================================================
def load_from_sheets(cfg):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("エラー: gspread / google-auth が必要です。")
        print("    pip install gspread google-auth")
        sys.exit(1)

    sheets_id = os.environ.get("SHEETS_ID") or cfg.get("sheets_id")
    if not sheets_id:
        raise ValueError("スプレッドシートIDが未指定です。SHEETS_ID 環境変数または build_config.json を確認してください。")

    cred_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    scopes   = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    if cred_env:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(json.loads(cred_env), scopes=scopes)
    else:
        cred_file = cfg.get("credentials_file", "credentials.json")
        if not os.path.isabs(cred_file):
            cred_file = os.path.join(SCRIPT_DIR, cred_file)
        if not os.path.exists(cred_file):
            raise FileNotFoundError(f"認証ファイルが見つかりません: {cred_file}")
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(cred_file, scopes=scopes)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheets_id)

    tab_name = os.environ.get("SHEETS_TAB") or cfg.get("sheets_tab")
    if not tab_name:
        all_titles = [ws.title for ws in sh.worksheets()]
        candidates = sorted([t for t in all_titles if t.startswith(SHEET_PREFIX)], reverse=True)
        if not candidates:
            raise ValueError(f"タブ '{SHEET_PREFIX}*' が見つかりません: {all_titles}")
        tab_name = candidates[0]

    print(f"  ソース: Sheets  ID: {sheets_id}  タブ: {tab_name}")
    ws   = sh.worksheet(tab_name)
    rows = ws.get_all_values()
    if not rows:
        raise ValueError("シートが空です")
    df = pd.DataFrame(rows[1:], columns=rows[0], dtype=str).fillna("")
    return df, tab_name


# ============================================================
# データソース振り分け
# ============================================================
def load_data():
    cfg    = load_config()
    source = (os.environ.get("DATA_SOURCE") or cfg.get("source", "csv")).lower()
    if source == "csv":
        return load_from_csv(cfg)
    elif source == "sheets":
        return load_from_sheets(cfg)
    else:
        return load_from_excel(EXCEL_FILE)


# ============================================================
# ユーザー権限カラム解析
# ============================================================
ROLE_MAP = {
    "owner": "オーナー", "organizer": "オーナー",
    "fileorganizer": "コンテンツ管理者",
    "writer": "編集者", "commenter": "コメント可", "reader": "閲覧者",
}

def normalize_role(role):
    if not role:
        return ""
    return ROLE_MAP.get(role.strip().lower(), role.strip())

def parse_permission_cell(cell):
    """
    "a@x.com(writer) / b@y.com(reader)" などを解析してリストを返す。
    区切り: " / " > 改行 > "/"
    """
    s = str(cell).strip()
    if not s:
        return []
    if " / " in s:
        parts = s.split(" / ")
    elif "\n" in s:
        parts = s.split("\n")
    elif "/" in s:
        parts = s.split("/")
    else:
        parts = [s]

    results = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", p)
        if m:
            email = m.group(1).strip()
            role  = normalize_role(m.group(2))
            results.append(f"{email} ({role})" if role else email)
        else:
            results.append(p)
    return results

def parse_users_from_row(row):
    """
    フォーマットA: 「ユーザー1」〜「ユーザー30」列
    フォーマットB: 「全権限」を含む列
    """
    users = []
    try:
        keys = list(row.index)
    except AttributeError:
        keys = list(row.keys()) if hasattr(row, "keys") else []

    perm_col = None
    for k in keys:
        if "全権限" in str(k) or "アクセス権限" in str(k):
            perm_col = k
            break
    if perm_col is None:
        for k in keys:
            if "権限" in str(k):
                perm_col = k
                break

    if perm_col is not None:
        cell = row.get(perm_col, "")
        if cell and str(cell).strip():
            users = parse_permission_cell(cell)

    if not users:
        for i in range(1, 31):
            u = str(row.get(f"ユーザー{i}", "")).strip()
            if u:
                m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", u)
                if m:
                    users.append(f"{m.group(1).strip()} ({normalize_role(m.group(2))})")
                else:
                    users.append(u)
    return users


# ============================================================
# ドライブ単位ツリー構築
# ============================================================
def build_folders_for_drive(drive_rows):
    folders    = []
    seen_paths = {}

    def ensure_folder(path_tuple):
        if path_tuple in seen_paths:
            return seen_paths[path_tuple]
        parent_id = ensure_folder(path_tuple[:-1]) if len(path_tuple) > 1 else None
        node_id   = len(folders)
        folders.append({
            "id": node_id, "name": path_tuple[-1],
            "level": len(path_tuple), "parent": parent_id,
            "url": "", "users": [],
        })
        seen_paths[path_tuple] = node_id
        return node_id

    for _, row in drive_rows.iterrows():
        raw = [str(row.get(f"階層{i}", "")).strip() for i in range(1, 7)]
        levels = [v for v in raw if v]
        if not levels:
            continue
        path_tuple = tuple(levels)
        users      = parse_users_from_row(row)
        url        = str(row.get("フォルダURL", "")).strip()
        node_id    = ensure_folder(path_tuple)
        node       = folders[node_id]
        for u in users:
            if u not in node["users"]:
                node["users"].append(u)
        if url and not node["url"]:
            node["url"] = url

    return folders


# ============================================================
# レイアウト計算
# ============================================================
def compute_layout(folders):
    if not folders:
        return 0
    children_map = defaultdict(list)
    for f in folders:
        if f["parent"] is not None:
            children_map[f["parent"]].append(f["id"])
    roots  = [f["id"] for f in folders if f["parent"] is None]
    layout = {}
    cursor = [TOP_MARGIN]

    def assign(node_id):
        kids = children_map.get(node_id, [])
        node = folders[node_id]
        x    = LEVEL_X[min(node["level"] - 1, len(LEVEL_X) - 1)]
        w    = LEVEL_W[min(node["level"] - 1, len(LEVEL_W) - 1)]
        if not kids:
            y = cursor[0]; cursor[0] += ROW_STEP
            layout[node_id] = {"x": x, "y": y, "w": w}
            return y
        child_ys = [assign(k) for k in kids]
        y = (min(child_ys) + max(child_ys)) / 2
        layout[node_id] = {"x": x, "y": y, "w": w}
        return y

    for r in roots:
        assign(r)
    for f in folders:
        f["layout"] = layout.get(f["id"], {"x": 0, "y": 0, "w": 200})
    return cursor[0] + BOTTOM_MARGIN


# ============================================================
# 全ドライブ構築
# ============================================================
def detect_drive_col(df):
    for col in ["共有ドライブ名", "管理名称(Sheet1 B列)", "ドライブ名", "管理名称"]:
        if col in df.columns:
            return col
    raise ValueError(
        f"ドライブ名カラムが見つかりません。\n"
        f"  実際のカラム: {list(df.columns)}"
    )

def build_drives(df):
    drives      = []
    drive_col   = detect_drive_col(df)
    print(f"  ドライブ名カラム: '{drive_col}'")
    grouped     = df.groupby(drive_col, sort=False)
    drive_index = 0

    for drive_name, group in grouped:
        if not str(drive_name).strip():
            continue
        drive_index += 1
        drive_id = f"{drive_index:03d}"
        folders  = build_folders_for_drive(group)

        if not folders:
            url = ""
            users_set = []
            for _, row in group.iterrows():
                u = str(row.get("フォルダURL", "")).strip()
                if u and not url:
                    url = u
                for ru in parse_users_from_row(row):
                    if ru not in users_set:
                        users_set.append(ru)
            folders = [{
                "id": 0, "name": str(drive_name), "level": 1,
                "parent": None, "url": url, "users": users_set,
            }]
            print(f"  [情報] {drive_name}: 階層情報なし → ルートのみで登録")

        svg_height = compute_layout(folders)
        root_url   = next((f["url"] for f in folders if f["level"] == 1 and f["url"]), "")
        all_users  = {u for f in folders for u in f["users"]}

        drives.append({
            "id": drive_id, "name": str(drive_name),
            "root_url": root_url,
            "folder_count": len(folders),
            "user_count": len(all_users),
            "svg_height": int(svg_height) + 1,
            "svg_width": SVG_WIDTH,
            "folders": folders,
        })

    return drives


# ============================================================
# ユーザー逆引きインデックス
# ============================================================
def parse_user_entry(entry):
    s = str(entry).strip()
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", s)
    return (m.group(1).strip(), m.group(2).strip()) if m else (s, "")

def build_user_index(drives):
    index = defaultdict(list)
    for drive in drives:
        for folder in drive["folders"]:
            for ue in folder["users"]:
                email, role = parse_user_entry(ue)
                index[email].append({
                    "drive_id": drive["id"],
                    "folder_id": folder["id"],
                    "role": role,
                })
    return dict(index)


# ============================================================
# data.js 出力
# ============================================================
def write_data_js(drives, user_index, sheet_name, output_path,
                  locked_drives=None, unlock_password=None):
    total_folders = sum(d["folder_count"] for d in drives)
    db = {
        "meta": {
            "generated_at":   datetime.now().isoformat(timespec="seconds"),
            "source_sheet":   sheet_name,
            "drive_count":    len(drives),
            "folder_count":   total_folders,
            "user_count":     len(user_index),
            "locked_drives":  locked_drives or [],
            "unlock_password": unlock_password or "",
        },
        "drives": drives,
        "users":  user_index,
    }
    json_str   = json.dumps(db, ensure_ascii=False, separators=(",", ":"))
    js_content = (
        "// ===========================================================\n"
        "// data.js — 自動生成ファイル (build.py により生成)\n"
        f"// 生成日時: {db['meta']['generated_at']}\n"
        f"// 元データ: {sheet_name}\n"
        "// このファイルは編集しないでください。再生成は build.py を実行。\n"
        "// ===========================================================\n"
        f"window.DRIVE_DB = {json_str};\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(js_content)
    return len(js_content)


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 60)
    print(" build.py — 共有ドライブビジュアライザ (CSV版) ビルド")
    print("=" * 60)

    print("\n[1/4] データ読み込み...")
    df, sheet_name = load_data()
    print(f"      行数: {len(df)}, 列数: {len(df.columns)}")
    print(f"      カラム: {list(df.columns)}")

    print("\n[2/4] ドライブ単位でツリー構築 + レイアウト計算...")
    drives = build_drives(df)
    print(f"      ドライブ数: {len(drives)}")
    print(f"      総フォルダ数: {sum(d['folder_count'] for d in drives)}")

    print("\n[3/4] ユーザー逆引きインデックス構築...")
    user_index = build_user_index(drives)
    print(f"      ユニークユーザー数: {len(user_index)}")
    # サンプル表示
    for d in drives:
        for f in d["folders"]:
            if f["users"]:
                print(f"      [サンプル] {d['name']} / {f['name']} → {f['users'][:2]}")
                break
        else:
            continue
        break
    else:
        print("      [警告] ユーザー情報が見つかりませんでした")

    print("\n[4/4] data.js 出力...")
    cfg             = load_config()
    locked_drives   = cfg.get("locked_drives", []) or []
    unlock_password = cfg.get("unlock_password", "") or ""
    if locked_drives:
        print(f"      ロック対象: {locked_drives}")
    size = write_data_js(drives, user_index, sheet_name, OUTPUT_FILE,
                         locked_drives=locked_drives,
                         unlock_password=unlock_password)
    print(f"      出力: {OUTPUT_FILE}")
    print(f"      サイズ: {size:,} 文字 ({size / 1024 / 1024:.2f} MB)")
    print("\n完了しました。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nエラー: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
