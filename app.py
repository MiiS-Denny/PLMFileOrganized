import io
import hmac
import re
import zipfile
import subprocess
import tempfile

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from pypdf import PdfReader, PdfWriter


# ============================================================
# SYSTEM CONFIG
# ============================================================

APP_NAME = "PLM PDF Automation Tool"
APP_VERSION = "1.3.0"

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
# DOCUMENT NUMBER DETECTION
# ============================================================

DOC_PATTERNS = [
    r"\bDOC[-\s_]?(\d{4})[-\s_]?(\d{5})\b",
]


def normalize_document_number(text):

    if not text:
        return None

    text = text.upper()

    for pattern in DOC_PATTERNS:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            year = match.group(1)
            number = match.group(2)

            return f"DOC-{year}-{number}"

    return None


# ============================================================
# PDF TEXT EXTRACTION
# ============================================================

def extract_pdf_text(pdf_path, max_pages=3):

    reader = PdfReader(
        str(pdf_path)
    )

    text_parts = []

    page_count = min(
        len(reader.pages),
        max_pages
    )

    for i in range(page_count):

        try:

            text = (
                reader.pages[i]
                .extract_text()
                or ""
            )

            text_parts.append(text)

        except Exception:
            pass

    return "\n".join(text_parts)


def detect_doc_number_from_pdf(pdf_path):

    text = extract_pdf_text(
        pdf_path,
        max_pages=3
    )

    return normalize_document_number(
        text
    )


# ============================================================
# WORD → PDF
# ============================================================

def word_to_pdf(
    word_path,
    output_dir
):

    output_dir = Path(
        output_dir
    )


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
            "Signed Cover PDF 必須只有 1 頁"
        )


    if len(main_reader.pages) < 1:

        raise RuntimeError(
            "Word PDF 沒有頁面"
        )


    writer = PdfWriter()


    # New cover
    writer.add_page(
        cover_reader.pages[0]
    )


    # Original page 2 ~ end
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
# ZIP
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
# LOGIN
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
# ANALYZE FILES
# ============================================================

def analyze_uploaded_files(
    uploaded_files
):

    word_files = []

    pdf_files = []


    for f in uploaded_files:

        suffix = Path(
            f.name
        ).suffix.lower()


        if suffix in [
            ".doc",
            ".docx"
        ]:

            word_files.append(f)


        elif suffix == ".pdf":

            pdf_files.append(f)


    return word_files, pdf_files


# ============================================================
# BUILD MATCHING PREVIEW
# ============================================================

def build_matching_preview(
    uploaded_files
):

    word_files, pdf_files = (
        analyze_uploaded_files(
            uploaded_files
        )
    )


    rows = []

    errors = []


    # --------------------------------------------------------
    # Basic count check
    # --------------------------------------------------------

    if len(word_files) != len(pdf_files):

        errors.append(
            (
                "數量不一致："
                f"Word = {len(word_files)}，"
                f"Signed Cover PDF = {len(pdf_files)}"
            )
        )


    if len(word_files) == 0:

        errors.append(
            "沒有找到 Word 檔案"
        )


    if len(pdf_files) == 0:

        errors.append(
            "沒有找到 Signed Cover PDF"
        )


    return (
        word_files,
        pdf_files,
        rows,
        errors
    )


# ============================================================
# PROCESS
# ============================================================

def run_process(
    uploaded_files
):

    word_files, cover_files = (
        analyze_uploaded_files(
            uploaded_files
        )
    )


    # --------------------------------------------------------
    # Count validation
    # --------------------------------------------------------

    if len(word_files) != len(cover_files):

        st.error(
            "無法開始：Word 與 Signed Cover PDF 數量不一致。"
        )

        return


    total = len(word_files)

    progress = st.progress(
        0,
        text="SYSTEM INITIALIZING..."
    )


    status = st.status(
        "ANALYZING FILES...",
        expanded=True,
    )


    result_files = []

    success = 0
    failed = 0


    with tempfile.TemporaryDirectory() as tmp:

        tmp = Path(tmp)


        # ====================================================
        # STEP 1
        # SAVE + ANALYZE COVERS
        # ====================================================

        cover_map = {}

        status.write(
            "→ Reading Signed Cover PDFs..."
        )


        for cover in cover_files:

            cover_path = (
                tmp
                / cover.name
            )


            cover_path.write_bytes(
                cover.getvalue()
            )


            try:

                reader = PdfReader(
                    str(cover_path)
                )

                if len(reader.pages) != 1:

                    raise RuntimeError(
                        (
                            f"{cover.name} "
                            "不是單頁 PDF"
                        )
                    )


                doc_no = (
                    detect_doc_number_from_pdf(
                        cover_path
                    )
                )


                if not doc_no:

                    raise RuntimeError(
                        (
                            f"{cover.name} "
                            "找不到 DOC 文件編號"
                        )
                    )


                if doc_no in cover_map:

                    raise RuntimeError(
                        (
                            "Signed Cover 文件編號重複："
                            f"{doc_no}"
                        )
                    )


                cover_map[
                    doc_no
                ] = {
                    "upload": cover,
                    "path": cover_path,
                    "name": cover.name,
                }


                status.write(
                    (
                        f"✓ COVER : "
                        f"{cover.name}"
                        f" → {doc_no}"
                    )
                )


            except Exception as e:

                status.update(
                    label="ANALYSIS FAILED",
                    state="error",
                    expanded=True,
                )

                st.error(str(e))

                return


        # ====================================================
        # STEP 2
        # SAVE + CONVERT + ANALYZE WORD
        # ====================================================

        word_map = {}


        status.write(
            "→ Converting and analyzing Word files..."
        )


        for word in word_files:

            word_path = (
                tmp
                / word.name
            )


            word_path.write_bytes(
                word.getvalue()
            )


            try:

                pdf_path = word_to_pdf(
                    word_path,
                    tmp
                )


                doc_no = (
                    detect_doc_number_from_pdf(
                        pdf_path
                    )
                )


                if not doc_no:

                    raise RuntimeError(
                        (
                            f"{word.name} "
                            "找不到 DOC 文件編號"
                        )
                    )


                if doc_no in word_map:

                    raise RuntimeError(
                        (
                            "Word 文件編號重複："
                            f"{doc_no}"
                        )
                    )


                word_map[
                    doc_no
                ] = {
                    "upload": word,
                    "word_path": word_path,
                    "pdf_path": pdf_path,
                    "name": word.name,
                }


                status.write(
                    (
                        f"✓ WORD : "
                        f"{word.name}"
                        f" → {doc_no}"
                    )
                )


            except Exception as e:

                status.update(
                    label="ANALYSIS FAILED",
                    state="error",
                    expanded=True,
                )

                st.error(str(e))

                return


        # ====================================================
        # STEP 3
        # MATCH VALIDATION
        # ====================================================

        cover_keys = set(
            cover_map.keys()
        )

        word_keys = set(
            word_map.keys()
        )


        missing_cover = (
            word_keys
            - cover_keys
        )


        missing_word = (
            cover_keys
            - word_keys
        )


        if missing_cover or missing_word:

            status.update(
                label="MATCHING FAILED",
                state="error",
                expanded=True,
            )


            if missing_cover:

                st.error(
                    (
                        "以下 Word 找不到對應 Signed Cover：\n\n"
                        + "\n".join(
                            sorted(
                                missing_cover
                            )
                        )
                    )
                )


            if missing_word:

                st.error(
                    (
                        "以下 Signed Cover 找不到對應 Word：\n\n"
                        + "\n".join(
                            sorted(
                                missing_word
                            )
                        )
                    )
                )


            return


        # ====================================================
        # MATCHING PREVIEW
        # ====================================================

        mapping_rows = []


        for doc_no in sorted(
            word_keys
        ):

            mapping_rows.append(
                {
                    "DOCUMENT NO.":
                    doc_no,

                    "WORD FILE":
                    word_map[
                        doc_no
                    ][
                        "name"
                    ],

                    "SIGNED COVER":
                    cover_map[
                        doc_no
                    ][
                        "name"
                    ],

                    "STATUS":
                    "MATCHED",
                }
            )


        st.markdown(
            "### AUTO MATCHING RESULT"
        )


        st.dataframe(
            mapping_rows,
            use_container_width=True,
            hide_index=True,
        )


        # ====================================================
        # STEP 4
        # CREATE FINAL PDF
        # ====================================================

        status.write(
            "→ Replacing cover pages..."
        )


        for index, doc_no in enumerate(
            sorted(word_keys),
            start=1
        ):

            try:

                main_pdf = (
                    word_map[
                        doc_no
                    ][
                        "pdf_path"
                    ]
                )


                cover_pdf = (
                    cover_map[
                        doc_no
                    ][
                        "path"
                    ]
                )


                final_name = (
                    f"{doc_no}-Final.pdf"
                )


                final_path = (
                    tmp
                    / final_name
                )


                replace_first_page(
                    cover_pdf,
                    main_pdf,
                    final_path,
                )


                result_files.append(
                    (
                        final_name,
                        final_path.read_bytes(),
                    )
                )


                success += 1


                status.write(
                    (
                        f"✓ [{index}/{total}] "
                        f"{final_name}"
                    )
                )


            except Exception as e:

                failed += 1


                status.write(
                    (
                        f"✗ [{index}/{total}] "
                        f"{doc_no}: "
                        f"{e}"
                    )
                )


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
    # ZIP
    # ========================================================

    finished_at = datetime.now(
        TAIPEI_TZ
    )


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


# ============================================================
# DOWNLOAD
# ============================================================

def show_download_results():

    if not st.session_state.result_zip:
        return


    st.divider()

    st.subheader(
        "OUTPUT"
    )


    c1, c2, c3 = st.columns(
        3
    )


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
# MAIN
# ============================================================

def show_main_app():

    username = (
        st.session_state.username
    )


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


    st.markdown(
        """
        ### AUTO PDF COVER MATCHING

        一次拖入：

        - Word 文件
        - Signed Cover PDF

        Signed Cover **檔名可以任意**。

        系統會讀取文件內容中的：

        `DOC-YYYY-NNNNN`

        自動尋找對應 Word。

        注意：

        - 每份 Signed Cover 必須只有 1 頁
        - Word 與 Signed Cover 數量必須一致
        - 文件編號不可重複
        - 找不到對應文件時不會執行
        """
    )


    uploaded_files = st.file_uploader(
        "DROP WORD + SIGNED COVER PDF FILES HERE",
        type=[
            "doc",
            "docx",
            "pdf"
        ],
        accept_multiple_files=True,
    )


    if uploaded_files:

        word_files, pdf_files = (
            analyze_uploaded_files(
                uploaded_files
            )
        )


        c1, c2, c3 = st.columns(
            3
        )


        c1.metric(
            "WORD",
            len(word_files)
        )


        c2.metric(
            "SIGNED COVER",
            len(pdf_files)
        )


        count_ok = (
            len(word_files)
            == len(pdf_files)
            and len(word_files) > 0
        )


        c3.metric(
            "COUNT CHECK",
            (
                "OK"
                if count_ok
                else "ERROR"
            )
        )


        if not count_ok:

            st.error(
                (
                    "Word 與 Signed Cover PDF 數量必須完全一致。"
                    f"目前 Word = {len(word_files)}，"
                    f"PDF = {len(pdf_files)}"
                )
            )


        if st.button(
            "ANALYZE & START PROCESS",
            type="primary",
            use_container_width=True,
            disabled=not count_ok,
        ):

            run_process(
                uploaded_files
            )


    show_download_results()


# ============================================================
# START
# ============================================================

if not st.session_state.authenticated:

    show_login()

else:

    show_main_app()
