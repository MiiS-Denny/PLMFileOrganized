import io
import hmac
import zipfile
import subprocess
import tempfile

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter


# ============================================================
# SYSTEM CONFIG
# ============================================================

APP_NAME = "PLM PDF Automation Tool"
APP_VERSION = "1.2.0"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


st.set_page_config(
    page_title=APP_NAME,
    page_icon="📄",
    layout="wide",
)


# ============================================================
# UI STYLE
# ============================================================

st.markdown(
    """
    <style>

    .block-container {
        max-width: 1200px;
        padding-top: 1.8rem;
    }

    .main-title {
        font-size: 30px;
        font-weight: 700;
        margin-bottom: 2px;
    }

    .sub-title {
        color: #777;
        font-size: 14px;
        margin-bottom: 18px;
    }

    .system-tag {
        font-family: monospace;
        font-size: 13px;
        color: #666;
    }

    div[data-testid="stMetric"] {
        border: 1px solid #d9d9d9;
        border-radius: 6px;
        padding: 12px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_SESSION = {
    "authenticated": False,
    "username": None,

    "result_files": [],
    "result_zip": None,
    "result_zip_name": None,

    "last_success": 0,
    "last_failed": 0,
    "last_total": 0,
}


for key, value in DEFAULT_SESSION.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# USER DATABASE
#
# Streamlit Secrets:
#
# [users.Charles]
# password = "A00027"
#
# ============================================================

def get_user_db():

    try:

        users = st.secrets["users"]

        result = {}

        for username, data in users.items():

            result[username] = {
                "password": str(data["password"]).strip()
            }

        return result

    except Exception as e:

        st.error("無法讀取使用者設定。")

        st.code(str(e))

        st.stop()


USER_DB = get_user_db()


# ============================================================
# PASSWORD VERIFY
# ============================================================

def verify_password(username, password):

    if username not in USER_DB:
        return False

    expected = USER_DB[username]["password"]

    entered = str(password).strip()

    return hmac.compare_digest(
        entered,
        expected
    )


# ============================================================
# SUPABASE CONFIG
# ============================================================

def get_supabase_config():

    try:

        url = str(
            st.secrets["supabase"]["url"]
        ).rstrip("/")

        key = str(
            st.secrets["supabase"]["service_role_key"]
        )

        return url, key

    except Exception:

        return None, None


# ============================================================
# WRITE USAGE LOG
# ============================================================

def write_usage_log(
    username,
    started_at,
    finished_at,
    file_count,
    success_count,
    failed_count,
):

    url, key = get_supabase_config()

    if not url or not key:

        return False, "Supabase 尚未設定"


    duration = (
        finished_at - started_at
    ).total_seconds()


    payload = {

        "username": username,

        "started_at": started_at.isoformat(),

        "finished_at": finished_at.isoformat(),

        "file_count": int(file_count),

        "success_count": int(success_count),

        "failed_count": int(failed_count),

        "duration_seconds": round(
            duration,
            2
        ),

        "app_version": APP_VERSION,
    }


    headers = {

        "apikey": key,

        "Authorization": f"Bearer {key}",

        "Content-Type": "application/json",

        "Prefer": "return=minimal",
    }


    try:

        response = requests.post(

            f"{url}/rest/v1/usage_log",

            headers=headers,

            json=payload,

            timeout=15,
        )


        if response.status_code in [
            200,
            201,
            204
        ]:

            return True, None


        return (
            False,
            f"HTTP {response.status_code}: {response.text}"
        )


    except Exception as e:

        return False, str(e)


# ============================================================
# READ USAGE LOG
# ============================================================

def read_usage_logs(limit=1000):

    url, key = get_supabase_config()

    if not url or not key:
        return []


    headers = {

        "apikey": key,

        "Authorization": f"Bearer {key}",
    }


    params = {

        "select": "*",

        "order": "started_at.desc",

        "limit": limit,
    }


    try:

        response = requests.get(

            f"{url}/rest/v1/usage_log",

            headers=headers,

            params=params,

            timeout=15,
        )


        if response.status_code == 200:

            return response.json()


    except Exception:

        pass


    return []


# ============================================================
# WORD → PDF
# LibreOffice Headless
# ============================================================

def word_to_pdf(
    word_path,
    output_dir
):

    output_dir = Path(output_dir)


    command = [

        "libreoffice",

        "--headless",

        "--convert-to",

        "pdf",

        "--outdir",

        str(output_dir),

        str(word_path),
    ]


    result = subprocess.run(

        command,

        stdout=subprocess.PIPE,

        stderr=subprocess.PIPE,

        text=True,

        timeout=180,
    )


    expected_pdf = (
        output_dir
        / f"{Path(word_path).stem}.pdf"
    )


    if not expected_pdf.exists():

        raise RuntimeError(
            "Word → PDF 失敗\n\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}"
        )


    return expected_pdf


# ============================================================
# REPLACE FIRST PAGE
#
# Cover PDF 第一頁
# +
# Word PDF 第 2 頁以後
# ============================================================

def replace_first_page(
    cover_pdf,
    main_pdf,
    final_pdf,
):

    cover_reader = PdfReader(
        str(cover_pdf)
    )

    main_reader = PdfReader(
        str(main_pdf)
    )


    if len(cover_reader.pages) != 1:

        raise RuntimeError(
            "Cover PDF 必須只有 1 頁"
        )


    if len(main_reader.pages) < 1:

        raise RuntimeError(
            "Word PDF 沒有任何頁面"
        )


    writer = PdfWriter()


    # --------------------------------------------------------
    # NEW COVER
    # --------------------------------------------------------

    writer.add_page(
        cover_reader.pages[0]
    )


    # --------------------------------------------------------
    # ORIGINAL PDF PAGE 2 ~ END
    # --------------------------------------------------------

    for page_index in range(
        1,
        len(main_reader.pages)
    ):

        writer.add_page(
            main_reader.pages[
                page_index
            ]
        )


    with open(
        final_pdf,
        "wb"
    ) as f:

        writer.write(f)


# ============================================================
# CREATE ZIP
# ============================================================

def create_zip(result_files):

    zip_buffer = io.BytesIO()


    with zipfile.ZipFile(

        zip_buffer,

        "w",

        zipfile.ZIP_DEFLATED,

    ) as zf:

        for filename, data in result_files:

            zf.writestr(
                filename,
                data,
            )


    zip_buffer.seek(0)

    return zip_buffer.getvalue()


# ============================================================
# LOGIN PAGE
# ============================================================

def show_login():

    st.markdown(
        f"""
        <div class="main-title">
            {APP_NAME}
        </div>

        <div class="sub-title">
            Production Engineering Utility /
            Version {APP_VERSION}
        </div>
        """,
        unsafe_allow_html=True,
    )


    left, center, right = st.columns(
        [1.3, 1, 1.3]
    )


    with center:

        st.subheader(
            "USER LOGIN"
        )


        st.caption(
            "Authorized Personnel Only"
        )


        username = st.selectbox(

            "Account",

            options=sorted(
                USER_DB.keys()
            ),

            index=None,

            placeholder="Select account",
        )


        password = st.text_input(

            "Password / Employee ID",

            type="password",

            placeholder="Enter employee ID",
        )


        if st.button(

            "LOGIN",

            type="primary",

            use_container_width=True,

        ):

            if not username:

                st.warning(
                    "請選擇帳號"
                )

                return


            if not password:

                st.warning(
                    "請輸入密碼"
                )

                return


            if verify_password(
                username,
                password
            ):

                st.session_state.authenticated = True

                st.session_state.username = username

                st.rerun()


            else:

                st.error(
                    "帳號或密碼錯誤"
                )


# ============================================================
# ADMIN DASHBOARD
# ============================================================

def show_admin_dashboard():

    st.subheader(
        "SYSTEM USAGE DASHBOARD"
    )


    logs = read_usage_logs()


    if not logs:

        st.info(
            "尚無使用紀錄，或 Supabase 尚未設定。"
        )

        return


    df = pd.DataFrame(logs)


    # ========================================================
    # TIME CONVERSION
    # ========================================================

    if "started_at" in df.columns:

        df["started_at"] = pd.to_datetime(

            df["started_at"],

            errors="coerce",

            utc=True,

        ).dt.tz_convert(
            "Asia/Taipei"
        )


        df["使用日期"] = (
            df["started_at"]
            .dt.strftime(
                "%Y-%m-%d"
            )
        )


        df["使用時間"] = (
            df["started_at"]
            .dt.strftime(
                "%H:%M:%S"
            )
        )


    # ========================================================
    # SUMMARY
    # ========================================================

    summary = (

        df

        .groupby("username")

        .agg(

            使用次數=(
                "id",
                "count"
            ),

            處理檔案數=(
                "file_count",
                "sum"
            ),

            成功數=(
                "success_count",
                "sum"
            ),

            失敗數=(
                "failed_count",
                "sum"
            ),

            總處理秒數=(
                "duration_seconds",
                "sum"
            ),
        )

        .reset_index()
    )


    summary.rename(

        columns={
            "username": "使用者"
        },

        inplace=True
    )


    # ========================================================
    # METRICS
    # ========================================================

    c1, c2, c3 = st.columns(3)


    c1.metric(
        "總使用次數",
        len(df)
    )


    c2.metric(
        "總處理檔案",
        int(
            df["file_count"].sum()
        )
    )


    c3.metric(
        "使用人數",
        int(
            df["username"].nunique()
        )
    )


    st.markdown(
        "#### USER SUMMARY"
    )


    st.dataframe(

        summary,

        use_container_width=True,

        hide_index=True,
    )


    st.markdown(
        "#### RECENT ACTIVITY"
    )


    display_columns = [

        column

        for column in [

            "username",

            "使用日期",

            "使用時間",

            "file_count",

            "success_count",

            "failed_count",

            "duration_seconds",

            "app_version",

        ]

        if column in df.columns
    ]


    display_df = df[
        display_columns
    ].copy()


    display_df.rename(

        columns={

            "username":
            "使用者",

            "file_count":
            "處理數量",

            "success_count":
            "成功",

            "failed_count":
            "失敗",

            "duration_seconds":
            "處理時間(秒)",

            "app_version":
            "版本",
        },

        inplace=True
    )


    st.dataframe(

        display_df,

        use_container_width=True,

        hide_index=True,
    )


# ============================================================
# PROCESS FILES
# ============================================================

def run_process(
    username,
    uploaded_files,
):

    started_at = datetime.now(
        TAIPEI_TZ
    )


    # ========================================================
    # FIND WORD FILES
    # ========================================================

    word_files = [

        f

        for f in uploaded_files

        if Path(
            f.name
        ).suffix.lower()

        in [
            ".doc",
            ".docx"
        ]
    ]


    # ========================================================
    # FIND COVER FILES
    # ========================================================

    cover_files = [

        f

        for f in uploaded_files

        if (

            Path(
                f.name
            ).suffix.lower()
            == ".pdf"

            and

            f.name.lower().startswith(
                "cover-"
            )
        )
    ]


    cover_map = {

        f.name.lower(): f

        for f in cover_files
    }


    total = len(word_files)

    success = 0

    failed = 0

    result_files = []


    # ========================================================
    # UI
    # ========================================================

    progress = st.progress(

        0,

        text="SYSTEM INITIALIZING..."
    )


    status = st.status(

        "PROCESSING...",

        expanded=True,
    )


    # ========================================================
    # TEMP WORKSPACE
    # ========================================================

    with tempfile.TemporaryDirectory() as tmp:

        tmp = Path(tmp)


        for index, word in enumerate(

            word_files,

            start=1

        ):

            base = Path(
                word.name
            ).stem


            expected_cover = (
                f"cover-{base}.pdf"
            )


            status.write(
                f"[{index}/{total}] {word.name}"
            )


            try:

                # =================================================
                # COVER CHECK
                # =================================================

                if (
                    expected_cover.lower()
                    not in cover_map
                ):

                    raise FileNotFoundError(

                        "Cover not found: "
                        + expected_cover
                    )


                # =================================================
                # SAVE WORD
                # =================================================

                word_path = (
                    tmp
                    / word.name
                )


                word_path.write_bytes(
                    word.getvalue()
                )


                # =================================================
                # SAVE COVER
                # =================================================

                cover_file = (
                    cover_map[
                        expected_cover.lower()
                    ]
                )


                cover_path = (
                    tmp
                    / expected_cover
                )


                cover_path.write_bytes(
                    cover_file.getvalue()
                )


                # =================================================
                # WORD → PDF
                # =================================================

                status.write(
                    "→ Converting Word to PDF..."
                )


                main_pdf = word_to_pdf(
                    word_path,
                    tmp
                )


                # =================================================
                # COVER REPLACEMENT
                # =================================================

                status.write(
                    "→ Replacing Cover Page..."
                )


                final_name = (
                    f"{base}-Final.pdf"
                )


                final_path = (
                    tmp
                    / final_name
                )


                replace_first_page(

                    cover_path,

                    main_pdf,

                    final_path,
                )


                # =================================================
                # MEMORY
                # =================================================

                result_files.append(

                    (

                        final_name,

                        final_path.read_bytes(),

                    )
                )


                success += 1


                status.write(
                    f"✓ COMPLETE : {final_name}"
                )


            except Exception as e:

                failed += 1


                status.write(
                    f"✗ FAILED : {word.name}"
                )


                status.write(
                    str(e)
                )


            # =================================================
            # PROGRESS
            # =================================================

            percent = int(
                index
                / total
                * 100
            )


            progress.progress(

                percent,

                text=(
                    f"{index} / "
                    f"{total} "
                    f"({percent}%)"
                ),
            )


    # ========================================================
    # FINISHED TIME
    # ========================================================

    finished_at = datetime.now(
        TAIPEI_TZ
    )


    # ========================================================
    # ZIP
    # ========================================================

    timestamp = (
        finished_at.strftime(
            "%Y%m%d_%H%M%S"
        )
    )


    zip_name = (
        f"PLM_PDF_"
        f"{timestamp}.zip"
    )


    zip_data = create_zip(
        result_files
    )


    # ========================================================
    # SESSION RESULTS
    # ========================================================

    st.session_state.result_files = (
        result_files
    )

    st.session_state.result_zip = (
        zip_data
    )

    st.session_state.result_zip_name = (
        zip_name
    )

    st.session_state.last_success = (
        success
    )

    st.session_state.last_failed = (
        failed
    )

    st.session_state.last_total = (
        total
    )


    # ========================================================
    # USAGE LOG
    # ========================================================

    log_ok, log_error = write_usage_log(

        username=username,

        started_at=started_at,

        finished_at=finished_at,

        file_count=total,

        success_count=success,

        failed_count=failed,
    )


    # ========================================================
    # STATUS COMPLETE
    # ========================================================

    status.update(

        label=(

            "PROCESS COMPLETE "

            f"| SUCCESS: {success} "

            f"| FAILED: {failed}"
        ),

        state=(

            "complete"

            if failed == 0

            else "error"
        ),

        expanded=True,
    )


    if not log_ok:

        st.warning(

            "PDF 已完成，但使用紀錄未寫入："

            + str(log_error)
        )


# ============================================================
# DOWNLOAD RESULTS
# ============================================================

def show_download_results():

    if not st.session_state.result_zip:
        return


    st.divider()


    st.subheader(
        "OUTPUT"
    )


    c1, c2, c3 = st.columns(3)


    c1.metric(
        "TOTAL",
        st.session_state.last_total
    )


    c2.metric(
        "SUCCESS",
        st.session_state.last_success
    )


    c3.metric(
        "FAILED",
        st.session_state.last_failed
    )


    # ========================================================
    # DOWNLOAD ZIP
    # ========================================================

    st.download_button(

        label=(
            "DOWNLOAD ALL "
            f"({st.session_state.last_success} FILES)"
        ),

        data=(
            st.session_state.result_zip
        ),

        file_name=(
            st.session_state.result_zip_name
        ),

        mime="application/zip",

        type="primary",

        use_container_width=True,
    )


    # ========================================================
    # INDIVIDUAL FILE DOWNLOADS
    # ========================================================

    with st.expander(
        "INDIVIDUAL PDF DOWNLOADS"
    ):

        for filename, data in (
            st.session_state.result_files
        ):

            st.download_button(

                label=filename,

                data=data,

                file_name=filename,

                mime="application/pdf",

                key=(
                    "download_"
                    + filename
                ),
            )


# ============================================================
# MAIN APP
# ============================================================

def show_main_app():

    username = (
        st.session_state.username
    )


    # ========================================================
    # HEADER
    # ========================================================

    col1, col2 = st.columns(
        [4, 1]
    )


    with col1:

        st.markdown(
            f"""
            <div class="main-title">
                {APP_NAME}
            </div>

            <div class="sub-title">
                Production Engineering Utility /
                Version {APP_VERSION}
            </div>
            """,
            unsafe_allow_html=True,
        )


    with col2:

        st.write(
            f"USER : **{username}**"
        )


        if st.button(

            "LOGOUT",

            use_container_width=True

        ):

            st.session_state.authenticated = False

            st.session_state.username = None

            st.session_state.result_files = []

            st.session_state.result_zip = None

            st.session_state.result_zip_name = None

            st.rerun()


    st.divider()


    # ========================================================
    # INSTRUCTIONS
    # ========================================================

    st.markdown(
        """
        ### PDF COVER REPLACEMENT

        將 **Word + Cover PDF** 一次全部拖入。

        命名規則：

        ```
        DOC-2026-00100.docx

        cover-DOC-2026-00100.pdf
        ```

        系統會產生：

        ```
        DOC-2026-00100-Final.pdf
        ```
        """
    )


    # ========================================================
    # FILE UPLOAD
    # ========================================================

    uploaded_files = st.file_uploader(

        "DROP WORD + COVER PDF FILES HERE",

        type=[
            "doc",
            "docx",
            "pdf"
        ],

        accept_multiple_files=True,
    )


    # ========================================================
    # FILE MAPPING
    # ========================================================

    if uploaded_files:

        word_files = [

            f

            for f in uploaded_files

            if Path(
                f.name
            ).suffix.lower()

            in [
                ".doc",
                ".docx"
            ]
        ]


        cover_files = [

            f

            for f in uploaded_files

            if (

                Path(
                    f.name
                ).suffix.lower()
                == ".pdf"

                and

                f.name.lower().startswith(
                    "cover-"
                )
            )
        ]


        cover_names = {

            f.name.lower()

            for f in cover_files
        }


        preview = []


        for word in word_files:

            base = Path(
                word.name
            ).stem


            expected = (
                f"cover-{base}.pdf"
            )


            preview.append(
                {

                    "WORD FILE":
                    word.name,

                    "EXPECTED COVER":
                    expected,

                    "STATUS":
                    (
                        "OK"

                        if expected.lower()
                        in cover_names

                        else "MISSING"
                    )
                }
            )


        st.markdown(
            "### FILE MAPPING"
        )


        st.dataframe(

            preview,

            use_container_width=True,

            hide_index=True,
        )


        missing_count = sum(

            1

            for row in preview

            if row["STATUS"]
            == "MISSING"
        )


        # ====================================================
        # METRICS
        # ====================================================

        c1, c2, c3 = st.columns(3)


        c1.metric(
            "WORD",
            len(word_files)
        )


        c2.metric(
            "COVER",
            len(cover_files)
        )


        c3.metric(
            "MISSING COVER",
            missing_count
        )


        # ====================================================
        # START
        # ====================================================

        start_disabled = (

            len(word_files) == 0

            or

            missing_count > 0
        )


        if st.button(

            "START PROCESS",

            type="primary",

            use_container_width=True,

            disabled=start_disabled,

        ):

            run_process(

                username,

                uploaded_files,
            )


    # ========================================================
    # DOWNLOAD
    # ========================================================

    show_download_results()


    # ========================================================
    # ADMIN
    # ONLY DENNY
    # ========================================================

    if username == "Denny":

        st.divider()


        with st.expander(
            "ADMIN / USAGE HISTORY"
        ):

            show_admin_dashboard()


# ============================================================
# APP START
# ============================================================

if not st.session_state.authenticated:

    show_login()

else:

    show_main_app()
