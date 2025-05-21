import uuid
from flask import Flask, request, render_template, send_file, jsonify
import pandas as pd
import tempfile
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # セッションを使う場合に必要

# 一時ファイルのパスを保持するためのグローバル辞書（実際はDBやキャッシュ利用も検討）
extracted_files = {}

# --- 部門リストの定義（建設コンサルタント用） ---
CONSTRUCTION_DEPARTMENTS = [
    "河川、砂防及び海岸・海洋部門", "港湾及び空港部門", "電力土木部門", "道路部門",
    "鉄道部門", "上水道及び工業用水道部門", "下水道部門", "農業土木部門",
    "森林土木部門", "水産土木部門", "造園部門", "都市計画及び地方計画部門",
    "地質部門", "土質及び基礎部門", "鋼構造及びコンクリート部門", "トンネル部門",
    "施工計画、施工設備及び積算部門", "建設環境部門", "機械部門", "電気電子部門", "廃棄物部門"
]

# ========= 建設コンサルタント抽出関数群 =========

def search_by_department_with_usage_and_engineers(file_path, department_name, file_type):
    sheet_data = pd.read_excel(file_path, sheet_name=0)
    relevant_columns = [
        '登録部門別使用人数＿名称', '商号又は名称', '資本金',
        '登録部門別使用人数＿合計', '登録部門別使用人数＿資格_技術士_当該部門', '営業所所在地'
    ]
    filtered_data = sheet_data[relevant_columns]
    department_data = filtered_data[filtered_data['登録部門別使用人数＿名称'] == department_name].copy()

    # 資本金は各社の最初の出現値を採用
    first_capital = sheet_data.groupby('商号又は名称', as_index=False)['資本金'].first()
    department_data = department_data.merge(first_capital, on='商号又は名称', how='left', suffixes=("", "_cap"))
    department_data.drop(columns=['資本金_cap'], inplace=True)

    # 本社住所の取得（各社最初の営業所所在地を本社住所とする）
    first_address = sheet_data.groupby('商号又は名称', as_index=False)['営業所所在地'].first()
    department_data = department_data.merge(first_address, on='商号又は名称', how='left', suffixes=("", "_first"))
    department_data = department_data.drop(columns=['営業所所在地'])
    department_data = department_data.rename(columns={'営業所所在地_first': '本社住所'})

    # 「登録部門別使用人数＿合計」を「登録部門使用人数」にリネーム
    department_data = department_data.rename(columns={'登録部門別使用人数＿合計': '登録部門使用人数'})

    # 技術士者数の計算
    def get_engineers_count(row):
        company_name = row['商号又は名称']
        dept = row['登録部門別使用人数＿名称']
        matching_rows = sheet_data[
            (sheet_data['商号又は名称'] == company_name) &
            (sheet_data['登録部門別使用人数＿名称'] == dept)
        ]
        if not matching_rows.empty:
            return int(round(matching_rows['登録部門別使用人数＿資格_技術士_当該部門'].sum()))
        return 0

    department_data['技術士者数'] = department_data.apply(get_engineers_count, axis=1)
    department_data.drop(columns=['登録部門別使用人数＿資格_技術士_当該部門'], inplace=True)
    department_data = department_data.sort_values(by='資本金', ascending=False)
    return department_data

def search_construction_multiple(file_path, selected_departments, search_mode):
    sheet_data = pd.read_excel(file_path, sheet_name=0)
    relevant_columns = [
        '登録部門別使用人数＿名称', '商号又は名称', '資本金',
        '登録部門別使用人数＿合計', '登録部門別使用人数＿資格_技術士_当該部門', '営業所所在地'
    ]
    data = sheet_data[relevant_columns]
    filtered = data[data['登録部門別使用人数＿名称'].isin(selected_departments)].copy()
    filtered = filtered.rename(columns={'登録部門別使用人数＿合計': '登録部門使用人数'})

    def get_engineers_count(row):
        company_name = row['商号又は名称']
        dept = row['登録部門別使用人数＿名称']
        matching = sheet_data[
            (sheet_data['商号又は名称'] == company_name) &
            (sheet_data['登録部門別使用人数＿名称'] == dept)
        ]
        if not matching.empty:
            return int(round(matching['登録部門別使用人数＿資格_技術士_当該部門'].iloc[0]))
        return 0

    filtered['技術士者数'] = filtered.apply(get_engineers_count, axis=1)

    # AND検索の場合：全ての部門を含む企業のみ抽出
    if search_mode == "and":
        companies = data.groupby('商号又は名称')['登録部門別使用人数＿名称'].apply(lambda s: list(pd.unique(s))).reset_index()
        companies_with_all = companies[
            companies['登録部門別使用人数＿名称'].apply(lambda s: set(selected_departments).issubset(s))
        ]['商号又は名称']
        filtered = filtered[filtered['商号又は名称'].isin(companies_with_all)]

    # 集約
    def aggregate_dept_and_engineers(group):
        depts = pd.unique(group['登録部門別使用人数＿名称'])
        sub = group.drop_duplicates(subset='登録部門別使用人数＿名称', keep='first')
        counts = sub['技術士者数'].tolist()
        return pd.Series({
            '建設コンサル部門': ', '.join(depts),
            '技術士者数': ', '.join(map(str, counts))
        })

    base_agg = filtered.groupby('商号又は名称').agg({
        '資本金': 'first',
        '登録部門使用人数': 'sum'
    }).reset_index()

    details = filtered.groupby('商号又は名称').apply(aggregate_dept_and_engineers).reset_index()

    address_df = sheet_data.groupby('商号又は名称', as_index=False)['営業所所在地'].first()

    grouped = pd.merge(base_agg, details, on='商号又は名称', how='left')
    grouped = pd.merge(grouped, address_df, on='商号又は名称', how='left')
    grouped = grouped.rename(columns={'営業所所在地': '本社住所'})
    grouped = grouped.sort_values(by='資本金', ascending=False)
    return grouped

# ========= 地質会社抽出 =========
def search_geology_all(file_path):
    sheet_data = pd.read_excel(file_path, sheet_name=0)
    relevant_columns = ['商号又は名称', '資本金', '技術関係＿合計', '営業所所在地']
    df = sheet_data[relevant_columns]
    sales_df = df.groupby('商号又は名称', as_index=False)['資本金'].first()
    tech_df = df.groupby('商号又は名称', as_index=False)['技術関係＿合計'].sum()
    tech_df = tech_df.rename(columns={'技術関係＿合計': '地質会社＿技術関係＿合計'})
    addr_df = df.groupby('商号又は名称', as_index=False)['営業所所在地'].first()
    result = pd.merge(sales_df, tech_df, on='商号又は名称', how='left')
    result = pd.merge(result, addr_df, on='商号又は名称', how='left')
    result = result.rename(columns={'営業所所在地': '本社住所'})
    result = result.sort_values(by='資本金', ascending=False)
    return result

# ========= 測量会社抽出 =========
def search_survey_all(file_path):
    """
    測量会社.xlsx から「商号又は名称」「資本金」「営業所所在地」を抽出。
    同じ会社が複数行ある場合は groupby して最初の行を採用。
    ただし、住所が入っていない最初の行を拾ってしまう場合があるなら、
    適宜カスタマイズが必要。（例: 'first_address_non_empty' 等）
    """
    sheet_data = pd.read_excel(file_path, sheet_name=0)
    df = sheet_data.groupby('商号又は名称', as_index=False).first()
    df = df[['商号又は名称', '資本金', '営業所所在地']]
    return df

# ========= 住所カラムを統合するユーティリティ関数 =========
def unify_address_columns(df, main_col='本社住所'):
    """
    df 内の住所カラム（本社住所_x, 本社住所_y, survey_address 等）を
    main_col（デフォルト: '本社住所'）にまとめる。
    """
    if main_col not in df.columns:
        df[main_col] = ""

    # 住所らしきカラムを探す
    address_cols = [
        c for c in df.columns
        if c != main_col and ('本社住所' in c or 'survey_address' in c)
    ]

    # main_col が空欄の行を他カラムで補完
    for c in address_cols:
        df[main_col] = df[main_col].mask(
            (df[main_col].isna()) | (df[main_col] == ""), 
            df[c]
        )

    # 統合後、不要な住所カラムを削除
    for c in address_cols:
        if c in df.columns:
            df.drop(columns=c, inplace=True)

    # NaN を空文字に
    df[main_col] = df[main_col].fillna("")
    return df

# ===========================
# 会社検索ページ用のルート
# ===========================

@app.route('/company_search')
def company_search():
    """
    会社検索のUIをレンダリングするルートです。
    templates/company_search.html を用意してください。
    """
    return render_template("company_search.html")


@app.route('/company_search/api')
def company_search_api():
    """
    AJAXで呼び出されるAPIエンドポイントです。
    GETパラメータ 'query' に基づいて、候補となる会社名の一覧をJSON形式で返します。
    ここではサンプル用にダミーデータを用いていますが、実際はデータベースやExcel等から抽出してください。
    """
    query = request.args.get("query", "").strip()
    
    # サンプル用の会社名リスト（実際の運用ではDB等から取得してください）
    companies = [
        "株式会社A",
        "株式会社B",
        "有限会社C",
        "株式会社デモ",
        "デモ有限会社",
        "サンプル株式会社",
        "例示会社"
    ]
    
    if query:
        # 入力文字列が含まれる会社名のみ抽出（部分一致）
        results = [company for company in companies if query in company]
    else:
        results = []
    
    return jsonify(results)

# ========= 総合抽出処理 =========
@app.route('/extract_all', methods=['POST'])
def extract_all():
    include_consultant = request.form.get('include_consultant') == 'yes'
    include_geology = request.form.get('include_geology') == 'yes'
    include_survey = request.form.get('include_survey') == 'yes'
    overall_mode = request.form.get('overall_mode')  # "or" または "and"

    df_cons = None
    df_geology = None
    df_survey = None

    # --- 建設コンサル抽出 ---
    if include_consultant:
        selected_departments = request.form.getlist('consultant_departments')
        consultant_mode = request.form.get('consultant_mode') if len(selected_departments) > 1 else None
        file_cons = os.path.join(app.root_path, "建設コンサル.xlsx")
        if selected_departments and selected_departments[0] != "含めない":
            if len(selected_departments) == 1:
                df_cons = search_by_department_with_usage_and_engineers(
                    file_cons, selected_departments[0], file_type='construction'
                )
            else:
                df_cons = search_construction_multiple(
                    file_cons, selected_departments, consultant_mode
                )
            df_cons = df_cons.rename(columns={
                "登録部門別使用人数＿名称": "建設コンサル部門",
                "登録部門使用人数": "建設コンサル登録部門使用人数"
            })

    # --- 地質会社抽出 ---
    if include_geology:
        file_geology = os.path.join(app.root_path, "地質会社.xlsx")
        df_geology = search_geology_all(file_geology)

    # --- 測量会社抽出 ---
    if include_survey:
        file_survey = os.path.join(app.root_path, "測量会社.xlsx")
        df_survey = search_survey_all(file_survey)

    # --- 測量会社のみ選択されたかどうか ---
    only_survey = include_survey and not include_consultant and not include_geology

    # マージ用リスト
    merge_dfs = []
    if include_consultant and df_cons is not None:
        merge_dfs.append(('cons', df_cons))
    if include_geology and df_geology is not None:
        merge_dfs.append(('geo', df_geology))
    if include_survey and df_survey is not None:
        if only_survey:
            # 単独選択の場合、営業所所在地を本社住所にリネーム
            df_survey = df_survey.rename(columns={"営業所所在地": "本社住所"})
            df_survey = df_survey[['商号又は名称', '資本金', '本社住所']]
        else:
            # 他と併用の場合は測量会社の住所を一時的に survey_address として保持
            df_survey = df_survey.rename(columns={"営業所所在地": "survey_address"})
            df_survey['測量会社'] = "〇"
            df_survey = df_survey[['商号又は名称', '資本金', '測量会社', 'survey_address']]
        merge_dfs.append(('survey', df_survey))

    if not merge_dfs:
        return "どちらの抽出も選択されていません", 400

    # --- 単一カテゴリの場合 ---
    if len(merge_dfs) == 1:
        tag, df_result = merge_dfs[0]
        if tag == 'survey' and only_survey:
            desired_order = ["商号又は名称", "資本金", "本社住所"]
            df_result = df_result[desired_order]
        elif tag == 'cons':
            desired_order = [
                "商号又は名称", "資本金",
                "建設コンサル登録部門使用人数", "技術士者数",
                "建設コンサル部門", "本社住所"
            ]
            df_result = df_result[desired_order]
        elif tag == 'geo':
            desired_order = [
                "商号又は名称", "資本金",
                "地質会社＿技術関係＿合計", "本社住所"
            ]
            df_result = df_result[desired_order]

    # --- 複数カテゴリの場合 ---
    else:
        merge_type = "inner" if overall_mode == "and" else "outer"

        # 先頭のデータフレームをベースに順次マージ
        df_result = merge_dfs[0][1]
        for i in range(1, len(merge_dfs)):
            tag, next_df = merge_dfs[i]
            df_result = pd.merge(
                df_result, next_df,
                on=["商号又は名称", "資本金"],
                how=merge_type,
                suffixes=("", "_dup")
            )
            # マージ直後に住所カラムを統合
            df_result = unify_address_columns(df_result, main_col="本社住所")

        # 出力カラムの順序を状況に合わせて設定
        # 3カテゴリ(建コン+地質+測量)
        if include_consultant and include_geology and include_survey:
            desired_order = [
                "商号又は名称", "資本金",
                "建設コンサル登録部門使用人数", "技術士者数",
                "建設コンサル部門", "地質会社＿技術関係＿合計",
                "測量会社", "本社住所"
            ]
        # 2カテゴリ(建コン+測量)
        elif include_consultant and include_survey and not include_geology:
            desired_order = [
                "商号又は名称", "資本金",
                "建設コンサル登録部門使用人数", "技術士者数",
                "建設コンサル部門", "測量会社", "本社住所"
            ]
        # 2カテゴリ(地質+測量)
        elif include_geology and include_survey and not include_consultant:
            desired_order = [
                "商号又は名称", "資本金",
                "測量会社", "地質会社＿技術関係＿合計", "本社住所"
            ]
        # 2カテゴリ(建コン+地質)
        elif include_consultant and include_geology:
            desired_order = [
                "商号又は名称", "資本金",
                "建設コンサル登録部門使用人数", "技術士者数",
                "建設コンサル部門", "地質会社＿技術関係＿合計", "本社住所"
            ]
        else:
            # 万一想定外の組み合わせなら、既存カラム順で出力
            desired_order = list(df_result.columns)

        # カラムが存在しない場合のエラーを回避
        existing_cols = [c for c in desired_order if c in df_result.columns]
        df_result = df_result.fillna("")
        df_result = df_result[existing_cols]

    # --- 資本金フィルター適用 ---
    df_result["資本金"] = pd.to_numeric(df_result["資本金"], errors="coerce")
    capital_min = request.form.get("capital_min", "").strip()
    capital_max = request.form.get("capital_max", "").strip()
    if capital_min:
        try:
            capital_min_val = float(capital_min)
            df_result = df_result[df_result["資本金"] >= capital_min_val]
        except ValueError:
            pass
    if capital_max:
        try:
            capital_max_val = float(capital_max)
            df_result = df_result[df_result["資本金"] <= capital_max_val]
        except ValueError:
            pass

    df_result = df_result.sort_values(by="資本金", ascending=False)

    # ========= プレビュー処理 =========
    # プレビュー用にDataFrameのコピーを作成
    preview_df = df_result.copy()

    # 「資本金」はそのまま残すので、数値カラムのうち「資本金」を除外して処理する
    numeric_cols = [col for col in preview_df.select_dtypes(include=['float', 'int']).columns if col != '資本金']
    for col in numeric_cols:
        preview_df[col] = preview_df[col].round(0).astype(int)

    # 上位1000件のみのHTMLテーブルに変換（プレビュー用）
    table_html = preview_df.head(1000).to_html(index=False)

    # 一時Excelファイルには元のdf_resultを保存（必要に応じて）
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        result_file_path = tmp.name
        df_result.to_excel(result_file_path, index=False)

    token = str(uuid.uuid4())
    extracted_files[token] = result_file_path
    count = len(df_result)

    # テンプレートにtable_htmlを渡し、画面下にプレビュー表示
    return render_template("extract_summary.html", count=count, token=token, table_html=table_html)

# ========= ダウンロード処理 =========
@app.route('/download/<token>', methods=['GET'])
def download(token):
    file_path = extracted_files.get(token)
    if file_path and os.path.exists(file_path):
        # ダウンロード後、一時ファイルを削除（必要に応じて）
        response = send_file(file_path, as_attachment=True, download_name="抽出結果.xlsx")
        # 必要に応じて extracted_files.pop(token, None) などで削除することも可能
        return response
    else:
        return "ファイルが見つかりませんでした", 404

# ========= メインページ (フォーム) =========
@app.route('/')
def extract_all_form():
    return render_template("combined_extraction.html", departments=CONSTRUCTION_DEPARTMENTS)

if __name__ == '__main__':
    app.run(debug=True)
