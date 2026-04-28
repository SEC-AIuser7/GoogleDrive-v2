#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 共有ドライブフォルダ構成データから data.js を生成するビルドスクリプト

使い方:
    python build.py

データソース:
  デフォルト: 同じフォルダの "共有ドライブフォルダ構成CSV.xlsx"
  オプション: build_config.json で Google Spreadsheet を指定可能

  build_config.json の例 (Sheets を使う場合):
  {
    "source": "sheets",
    "sheets_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz...",
    "sheets_tab": "出力結果_20260422_101010",
    "credentials_file": "credentials.json"
  }

  source を "excel" にするか build_config.json が無い場合は Excel を使用。

必要パッケージ:
    pip install pandas openpyxl
    (Sheets を使う場合は追加で) pip install gspread google-auth
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
    print("以下のコマンドを実行してください:")
    print("    pip install pandas openpyxl")
    sys.exit(1)

# ============================================================
# 設定
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(SCRIPT_DIR, "共有ドライブフォルダ構成CSV.xlsx")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "build_config.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "data.js")
SHEET_PREFIX = "出力結果_"  # 出力結果_YYYYMMDD_HHMMSS のシートを自動検出

# レイアウト定数 (drive.html の SVG 描画と一致させること)
NODE_HEIGHT = 22
NODE_GAP = 8           # 隣接ノード間の縦余白
ROW_STEP = NODE_HEIGHT + NODE_GAP  # 30
LEVEL_X = [20, 200, 420, 650, 880, 1100]      # 各レベルの x 座標
LEVEL_W = [170, 200, 200, 200, 200, 200]      # 各レベルの width
SVG_WIDTH = 1340
TOP_MARGIN = 30
BOTTOM_MARGIN = 30


# ============================================================
# 設定ファイル読み込み
# ============================================================
def load_config():
    """build_config.json があれば読み込む。なければデフォルト (Excel) を返す。"""
    if not os.path.exists(CONFIG_FILE):
        return {"source": "excel"}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "source" not in cfg:
            cfg["source"] = "excel"
        return cfg
    except Exception as e:
        print(f"  [警告] build_config.json の読み込みに失敗: {e}")
        print(f"  [警告] Excel ソースにフォールバックします")
        return {"source": "excel"}


# ============================================================
# データ読み込み: Excel / Sheets を抽象化
# ============================================================
def find_target_sheet_xls(excel_path):
    """Excel から 出力結果_YYYYMMDD_HHMMSS シートを自動検出"""
    xls = pd.ExcelFile(excel_path)
    candidates = [s for s in xls.sheet_names if s.startswith(SHEET_PREFIX)]
    if not candidates:
        raise ValueError(f"シート '{SHEET_PREFIX}*' が見つかりません。シート一覧: {xls.sheet_names}")
    candidates.sort(reverse=True)  # 最新を選択
    return candidates[0]


def load_from_excel(excel_path):
    """Excel から DataFrame を読み込む"""
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"Excel ファイルが見つかりません: {excel_path}")
    sheet_name = find_target_sheet_xls(excel_path)
    print(f"  ソース: Excel")
    print(f"  ファイル: {os.path.basename(excel_path)}")
    print(f"  シート: {sheet_name}")
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str)
    df = df.fillna("")
    return df, sheet_name


def load_from_sheets(cfg):
    """Google Spreadsheet から DataFrame を読み込む"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("エラー: gspread / google-auth がインストールされていません。")
        print("以下のコマンドを実行してください:")
        print("    pip install gspread google-auth")
        sys.exit(1)

    # 環境変数があればそちらを優先 (GitHub Actions 用)
    sheets_id = os.environ.get("SHEETS_ID") or cfg.get("sheets_id")
    if not sheets_id:
        raise ValueError(
            "スプレッドシートID が未指定です。\n"
            "  build_config.json の 'sheets_id' または環境変数 SHEETS_ID を設定してください。"
        )

    # 認証情報: 環境変数 GOOGLE_CREDENTIALS_JSON があればそれを優先 (JSON文字列)
    # なければ credentials_file からファイル読み込み
    cred_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if cred_env:
        try:
            cred_info = json.loads(cred_env)
        except json.JSONDecodeError as e:
            raise ValueError(f"環境変数 GOOGLE_CREDENTIALS_JSON の JSON パースに失敗: {e}")
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_info(cred_info, scopes=scopes)
    else:
        cred_file = cfg.get("credentials_file", "credentials.json")
        if not os.path.isabs(cred_file):
            cred_file = os.path.join(SCRIPT_DIR, cred_file)
        if not os.path.exists(cred_file):
            raise FileNotFoundError(
                f"認証ファイルが見つかりません: {cred_file}\n"
                f"  Google Cloud Console でサービスアカウントを作成し、JSONキーをダウンロードして配置してください。\n"
                f"  対象のスプレッドシートにそのサービスアカウントのメールアドレスを共有設定してください。\n"
                f"  (GitHub Actions 環境では環境変数 GOOGLE_CREDENTIALS_JSON で指定可能)"
            )
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_file(cred_file, scopes=scopes)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheets_id)

    # タブ名指定があればそれ、なければ "出力結果_*" を自動検出
    tab_name = os.environ.get("SHEETS_TAB") or cfg.get("sheets_tab")
    if not tab_name:
        all_titles = [ws.title for ws in sh.worksheets()]
        candidates = [t for t in all_titles if t.startswith(SHEET_PREFIX)]
        if not candidates:
            raise ValueError(f"タブ '{SHEET_PREFIX}*' が見つかりません。タブ一覧: {all_titles}")
        candidates.sort(reverse=True)
        tab_name = candidates[0]

    print(f"  ソース: Google Sheets")
    print(f"  Spreadsheet: {sheets_id}")
    print(f"  タブ: {tab_name}")

    ws = sh.worksheet(tab_name)
    # 全データを取得
    rows = ws.get_all_values()
    if not rows:
        raise ValueError("シートが空です")

    # 1行目をヘッダ、それ以降をデータ
    headers = rows[0]
    data_rows = rows[1:]
    df = pd.DataFrame(data_rows, columns=headers, dtype=str)
    df = df.fillna("")
    return df, tab_name


def load_data():
    """設定に応じて Excel または Sheets から読み込み。環境変数 DATA_SOURCE で上書き可能。"""
    cfg = load_config()
    # 環境変数があれば優先 (GitHub Actions 用)
    source = (os.environ.get("DATA_SOURCE") or cfg.get("source", "excel")).lower()

    if source == "sheets":
        return load_from_sheets(cfg)
    else:
        return load_from_excel(EXCEL_FILE)


# ============================================================
# ドライブ単位でツリー構築
# ============================================================
def build_folders_for_drive(drive_rows):
    """
    1ドライブ分の行データから、フォルダノード配列を構築。
    各フォルダには id, name, level, parent_id, url, users が含まれる。

    解釈ルール:
      各行の 階層1〜6 はそのフォルダのフルパス (左→右で深くなる)。
      末尾の非空セルがそのフォルダ自身の位置 (= level)、その手前が親パス。
      間に空セルがある場合は詰めて扱う (1件のみ存在する変則行を許容)。
      同じフルパスの行が複数あれば users をマージ。
      また、子フォルダの行から上位パスを推定し、未登場の中間階層も自動補完する。
    """
    folders = []
    seen_paths = {}  # tuple(path) -> folder index in folders[]

    def ensure_folder(path_tuple):
        """指定パスのフォルダが folders に登録されていなければ作る。親も再帰的に保証。"""
        if path_tuple in seen_paths:
            return seen_paths[path_tuple]
        parent_id = None
        if len(path_tuple) > 1:
            parent_id = ensure_folder(path_tuple[:-1])
        node_id = len(folders)
        folders.append({
            "id": node_id,
            "name": path_tuple[-1],
            "level": len(path_tuple),
            "parent": parent_id,
            "url": "",
            "users": [],
        })
        seen_paths[path_tuple] = node_id
        return node_id

    for _, row in drive_rows.iterrows():
        # 階層1〜6 を取得し、空セルを詰める (「末尾の非空までをフルパス」と解釈)
        raw_levels = [str(row.get(f"階層{i}", "")).strip() for i in range(1, 7)]
        # 末尾の空を切り捨て、間の空も詰める
        levels = [v for v in raw_levels if v]
        if not levels:
            continue

        path_tuple = tuple(levels)

        # ユーザー1〜30 を取得
        users = []
        for i in range(1, 31):
            u = str(row.get(f"ユーザー{i}", "")).strip()
            if u:
                users.append(u)

        url = str(row.get("フォルダURL", "")).strip()

        # フォルダ (および親) を保証
        node_id = ensure_folder(path_tuple)
        node = folders[node_id]

        # ユーザー追加マージ (重複排除)
        for u in users:
            if u not in node["users"]:
                node["users"].append(u)

        # URL は空なら採用
        if url and not node["url"]:
            node["url"] = url

    return folders


# ============================================================
# レイアウト計算 (Pythonで事前計算 - ハイブリッドB)
# ============================================================
def compute_layout(folders):
    """
    ツリー上の各ノードに x, y 座標を割り当てる。
    葉ノードは順に y を増やし、親ノードは子の y の中央に配置する。
    """
    if not folders:
        return 0

    # 子リスト構築
    children_map = defaultdict(list)
    for f in folders:
        if f["parent"] is not None:
            children_map[f["parent"]].append(f["id"])

    # ルートを特定
    roots = [f["id"] for f in folders if f["parent"] is None]

    # 葉ノードに y を割り当てつつ、親には子の中央 y を後付け
    layout = {}
    cursor = [TOP_MARGIN]  # mutable for closure

    def assign(node_id):
        node = folders[node_id]
        kids = children_map.get(node_id, [])
        if not kids:
            # 葉ノード
            y = cursor[0]
            cursor[0] += ROW_STEP
            x = LEVEL_X[min(node["level"] - 1, len(LEVEL_X) - 1)]
            w = LEVEL_W[min(node["level"] - 1, len(LEVEL_W) - 1)]
            layout[node_id] = {"x": x, "y": y, "w": w}
            return y
        else:
            # 子を先に配置
            child_ys = [assign(k) for k in kids]
            y = (min(child_ys) + max(child_ys)) / 2
            x = LEVEL_X[min(node["level"] - 1, len(LEVEL_X) - 1)]
            w = LEVEL_W[min(node["level"] - 1, len(LEVEL_W) - 1)]
            layout[node_id] = {"x": x, "y": y, "w": w}
            return y

    for r in roots:
        assign(r)

    # folders に layout を統合
    for f in folders:
        f["layout"] = layout.get(f["id"], {"x": 0, "y": 0, "w": 200})

    total_height = cursor[0] + BOTTOM_MARGIN
    return total_height


# ============================================================
# 全ドライブをグループ化して構築
# ============================================================
def build_drives(df):
    drives = []
    grouped = df.groupby("管理名称(Sheet1 B列)", sort=False)

    drive_index = 0
    for drive_name, group in grouped:
        if not drive_name or str(drive_name).strip() == "":
            continue
        drive_index += 1
        drive_id = f"{drive_index:03d}"

        folders = build_folders_for_drive(group)
        if not folders:
            print(f"  [警告] {drive_name}: 有効なフォルダなし、スキップ")
            continue

        svg_height = compute_layout(folders)

        # ルート URL (階層1のみのフォルダか、最初のフォルダ)
        root_url = ""
        for f in folders:
            if f["level"] == 1 and f["url"]:
                root_url = f["url"]
                break

        # ユニークユーザー数を計算
        all_users = set()
        for f in folders:
            for u in f["users"]:
                all_users.add(u)

        drives.append({
            "id": drive_id,
            "name": str(drive_name),
            "root_url": root_url,
            "folder_count": len(folders),
            "user_count": len(all_users),
            "svg_height": int(svg_height) + 1,
            "svg_width": SVG_WIDTH,
            "folders": folders,
        })

    return drives


# ============================================================
# ユーザー逆引きインデックス構築
# ============================================================
def build_user_index(drives):
    """ユーザー → アクセス可能フォルダ の逆引きを作成"""
    user_index = defaultdict(list)
    for drive in drives:
        drive_id = drive["id"]
        for folder in drive["folders"]:
            for user_entry in folder["users"]:
                # ユーザーエントリは "email" or "email (役割)" の形式の可能性がある
                # シンプルに email として扱う
                email, role = parse_user_entry(user_entry)
                user_index[email].append({
                    "drive_id": drive_id,
                    "folder_id": folder["id"],
                    "role": role,
                })
    return dict(user_index)


def parse_user_entry(entry):
    """ユーザーエントリから email と role を分離。'email (役割)' or 'email' 形式に対応"""
    s = str(entry).strip()
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


# ============================================================
# data.js 出力
# ============================================================
def write_data_js(drives, user_index, sheet_name, output_path):
    # 全体統計
    total_folders = sum(d["folder_count"] for d in drives)
    all_users = set(user_index.keys())

    db = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_sheet": sheet_name,
            "drive_count": len(drives),
            "folder_count": total_folders,
            "user_count": len(all_users),
        },
        "drives": drives,
        "users": user_index,
    }

    json_str = json.dumps(db, ensure_ascii=False, separators=(",", ":"))
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
    print(" build.py — 共有ドライブビジュアライザ ビルドスクリプト")
    print("=" * 60)

    print(f"\n[1/4] データ読み込み...")
    df, sheet_name = load_data()
    print(f"      行数: {len(df)}, 列数: {len(df.columns)}")

    print(f"\n[2/4] ドライブ単位でツリー構築 + レイアウト計算...")
    drives = build_drives(df)
    print(f"      ドライブ数: {len(drives)}")
    total_folders = sum(d["folder_count"] for d in drives)
    print(f"      総フォルダ数: {total_folders}")

    print(f"\n[3/4] ユーザー逆引きインデックス構築...")
    user_index = build_user_index(drives)
    print(f"      ユニークユーザー数: {len(user_index)}")

    print(f"\n[4/4] data.js 出力...")
    size = write_data_js(drives, user_index, sheet_name, OUTPUT_FILE)
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
