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
import pytesseract

from pypdf import PdfReader, PdfWriter
from pdf2image import convert_from_path


# ============================================================
# SYSTEM CONFIG
# ============================================================

APP_NAME = "PLM PDF Automation Tool"
APP_VERSION = "1.5.0"

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

    # Analysis
    "analysis_done": False,
    "analysis_matches": [],
    "analysis_errors": [],
    "analysis_word_count": 0,
    "analysis_cover_count": 0,

    # Output
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
                "password": str(
                    data["password"]
                ).strip()
            }

        return result

    except Exception as e:

        st.error(
            "無法讀取使用者設定。"
        )

        st.code(
            str(e)
        )

        st.stop()


USER_DB = get_user_db()


def verify_password(
    username,
    password
):

    if username not in USER_DB:

        return False

    expected = (
        USER_DB[username]["password"]
    )

    entered = str(
        password
    ).strip()

    return hmac.compare_digest(
        entered,
        expected
    )


# ============================================================
# DOCUMENT NUMBER DETECTION
# ============================================================

DOC_PATTERNS = [

    # DOC-2026-00491
    # DOC 2026 00491
    # DOC_2026_00491
    # D O C - 2026 - 00491

    r"D\s*O\s*C\s*[-_\s:]*([0-9]{4})\s*[-_\s:]*([0-9]{4,6})",
]


def normalize_document_number(
    text
):

    if not text:

        return None

    text = text.upper()

    text = text.replace(
        "\u00a0",
        " "
    )

    text = text.replace(
        "\u3000",
        " "
    )

    for pattern in DOC_PATTERNS:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            year = match.group(1)
            number = match.group(2)

            return (
                f"DOC-{year}-{number}"
            )

    return None


# ============================================================
# PDF TEXT EXTRACTION
# ============================================================

def extract_pdf_text(
    pdf_path,
    max_pages=3
):

    reader = PdfReader(
        str(pdf_path)
    )

    text_parts = []

    page_count = min(
        len(reader.pages),
        max_pages
    )

    for i in range(
        page_count
    ):

        try:

            text = (
                reader.pages[i]
                .extract_text()
                or ""
            )

            text_parts.append(
                text
            )

        except Exception:

            pass

    return "\n".join(
        text_parts
    )


# ============================================================
# OCR
# ============================================================

def extract_pdf_text_ocr(
    pdf_path
):

    images = convert_from_path(
        str(pdf_path),
        dpi=300,
        first_page=1,
        last_page=1,
        fmt="png",
        thread_count=1,
    )

    if not images:

        return ""

    image = images[0]


    # --------------------------------------------------------
    # OCR PASS 1
    # --------------------------------------------------------

    text1 = pytesseract.image_to_string(
        image,
        lang="eng",
        config="--psm 6"
    )


    if normalize_document_number(
        text1
    ):

        return text1


    # --------------------------------------------------------
    # OCR PASS 2
    # --------------------------------------------------------

    text2 = pytesseract.image_to_string(
        image,
        lang="eng",
        config="--psm 11"
    )


    return (
        text1
        + "\n"
        + text2
    )


# ============================================================
# SMART DOCUMENT NUMBER DETECTION
# ============================================================

def detect_doc_number_from_pdf(
    pdf_path,
    allow_ocr=True
):

    result = {
        "doc_no": None,
        "method": None,
        "debug_text": "",
    }


    # ========================================================
    # TEXT LAYER FIRST
    # ========================================================

    normal_text = extract_pdf_text(
        pdf_path,
        max_pages=3
    )


    doc_no = normalize_document_number(
        normal_text
    )


    if doc_no:

        result["doc_no"] = doc_no
        result["method"] = "TEXT"
        result["debug_text"] = normal_text

        return result


    # ========================================================
    # OCR FALLBACK
    # ========================================================

    if not allow_ocr:

        result["debug_text"] = normal_text

        return result


    try:

        ocr_text = extract_pdf_text_ocr(
            pdf_path
        )


        doc_no = normalize_document_number(
            ocr_text
        )


        result["debug_text"] = ocr_text


        if doc_no:

            result["doc_no"] = doc_no
            result["method"] = "OCR"


        return result


    except Exception as e:

        result["debug_text"] = (
            f"OCR ERROR: {e}"
        )

        return result


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

            f"FILE:\n"
            f"{Path(word_path).name}\n\n"

            f"STDOUT:\n"
            f"{result.stdout}\n\n"

            f"STDERR:\n"
            f"{result.stderr}"
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


    if len(
        cover_reader.pages
    ) != 1:

        raise RuntimeError(
            "Signed Cover PDF 必須只有 1 頁"
        )


    if len(
        main_reader.pages
    ) < 1:

        raise RuntimeError(
            "Word PDF 沒有頁面"
        )


    writer = PdfWriter()


    # Signed Cover
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

        writer.write(
            f
        )


# ============================================================
# ZIP
# ============================================================

def create_zip(
    result_files
):

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


    zip_buffer.seek(
        0
    )

    return zip_buffer.getvalue()


# ============================================================
# RESET
# ============================================================

def reset_analysis():

    st.session_state[
        "analysis_done"
    ] = False

    st.session_state[
        "analysis_matches"
    ] = []

    st.session_state[
        "analysis_errors"
    ] = []

    st.session_state[
        "analysis_word_count"
    ] = 0

    st.session_state[
        "analysis_cover_count"
    ] = 0


def reset_results():

    st.session_state[
        "result_files"
    ] = []

    st.session_state[
        "result_zip"
    ] = None

    st.session_state[
        "result_zip_name"
    ] = None

    st.session_state[
        "last_success"
    ] = 0

    st.session_state[
        "last_failed"
    ] = 0

    st.session_state[
        "last_total"
    ] = 0


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


    _, center, _ = st.columns(
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

                st.session_state[
                    "authenticated"
                ] = True

                st.session_state[
                    "username"
                ] = username

                st.rerun()

            else:

                st.error(
                    "帳號或密碼錯誤"
                )


# ============================================================
# FILE CLASSIFICATION
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

            word_files.append(
                f
            )


        elif suffix == ".pdf":

            pdf_files.append(
                f
            )


    return (
        word_files,
        pdf_files
    )


# ============================================================
# ANALYSIS PROCESS
# ============================================================

def run_analysis(
    uploaded_files
):

    reset_analysis()
    reset_results()


    word_files, cover_files = (
        analyze_uploaded_files(
            uploaded_files
        )
    )


    word_count = len(
        word_files
    )

    cover_count = len(
        cover_files
    )


    st.session_state[
        "analysis_word_count"
    ] = word_count

    st.session_state[
        "analysis_cover_count"
    ] = cover_count


    errors = []


    # ========================================================
    # COUNT WARNING
    # ========================================================

    if word_count != cover_count:

        errors.append(
            {
                "TYPE": "COUNT",
                "DOCUMENT NO.": "-",
                "FILE": "-",
                "ERROR": (
                    f"數量不一致：Word = {word_count}，"
                    f"Signed Cover = {cover_count}"
                ),
            }
        )


    if word_count == 0:

        errors.append(
            {
                "TYPE": "WORD",
                "DOCUMENT NO.": "-",
                "FILE": "-",
                "ERROR": "沒有找到 Word 文件",
            }
        )


    if cover_count == 0:

        errors.append(
            {
                "TYPE": "COVER",
                "DOCUMENT NO.": "-",
                "FILE": "-",
                "ERROR": "沒有找到 Signed Cover PDF",
            }
        )


    progress = st.progress(
        0,
        text="SYSTEM INITIALIZING..."
    )


    status = st.status(
        "ANALYZING FILES...",
        expanded=True,
    )


    # ========================================================
    # STORE MULTIPLE FILES PER DOC
    # ========================================================

    cover_candidates = {}
    word_candidates = {}


    total_steps = max(
        word_count + cover_count,
        1
    )

    current_step = 0


    with tempfile.TemporaryDirectory() as tmp_dir:

        tmp = Path(
            tmp_dir
        )


        # ====================================================
        # ANALYZE COVER
        # ====================================================

        status.write(
            "→ Reading Signed Cover PDFs..."
        )


        for index, cover in enumerate(
            cover_files,
            start=1
        ):

            current_step += 1


            cover_path = (
                tmp
                / (
                    f"cover_"
                    f"{index:04d}.pdf"
                )
            )


            cover_bytes = (
                cover.getvalue()
            )


            cover_path.write_bytes(
                cover_bytes
            )


            try:

                reader = PdfReader(
                    str(cover_path)
                )


                if len(
                    reader.pages
                ) != 1:

                    errors.append(
                        {
                            "TYPE": "COVER",
                            "DOCUMENT NO.": "-",
                            "FILE": cover.name,
                            "ERROR": (
                                "Signed Cover 不是單頁 PDF "
                                f"({len(reader.pages)} pages)"
                            ),
                        }
                    )


                    status.write(
                        (
                            f"✗ COVER {cover.name} "
                            "→ 不是單頁 PDF"
                        )
                    )


                    continue


                detection = (
                    detect_doc_number_from_pdf(
                        cover_path,
                        allow_ocr=True
                    )
                )


                doc_no = (
                    detection[
                        "doc_no"
                    ]
                )


                method = (
                    detection[
                        "method"
                    ]
                )


                if not doc_no:

                    debug_text = (
                        detection.get(
                            "debug_text",
                            ""
                        )
                    )


                    errors.append(
                        {
                            "TYPE": "COVER",
                            "DOCUMENT NO.": "-",
                            "FILE": cover.name,
                            "ERROR": (
                                "OCR / PDF Text "
                                "皆找不到 DOC 文件編號"
                            ),
                            "DEBUG": debug_text[:500],
                        }
                    )


                    status.write(
                        (
                            f"✗ COVER {cover.name} "
                            "→ 找不到 DOC 編號"
                        )
                    )


                    continue


                if doc_no not in cover_candidates:

                    cover_candidates[
                        doc_no
                    ] = []


                cover_candidates[
                    doc_no
                ].append(
                    {
                        "doc_no": doc_no,
                        "name": cover.name,
                        "bytes": cover_bytes,
                        "method": method,
                    }
                )


                status.write(
                    (
                        f"✓ COVER [{index}/{cover_count}] "
                        f"{cover.name} "
                        f"→ {doc_no} [{method}]"
                    )
                )


            except Exception as e:

                errors.append(
                    {
                        "TYPE": "COVER",
                        "DOCUMENT NO.": "-",
                        "FILE": cover.name,
                        "ERROR": str(e),
                    }
                )


                status.write(
                    (
                        f"✗ COVER {cover.name} "
                        f"→ {e}"
                    )
                )


            finally:

                percent = int(
                    current_step
                    / total_steps
                    * 100
                )


                progress.progress(
                    percent,
                    text=(
                        f"ANALYZING "
                        f"{current_step}/{total_steps}"
                    ),
                )


        # ====================================================
        # COVER DUPLICATES
        # ====================================================

        for doc_no, items in (
            cover_candidates.items()
        ):

            if len(items) > 1:

                file_names = ", ".join(
                    item["name"]
                    for item in items
                )


                errors.append(
                    {
                        "TYPE": "COVER",
                        "DOCUMENT NO.": doc_no,
                        "FILE": file_names,
                        "ERROR": (
                            "Signed Cover 文件編號重複，"
                            "此編號不會自動執行"
                        ),
                    }
                )


        # ====================================================
        # ANALYZE WORD
        # ====================================================

        status.write(
            "→ Converting and analyzing Word files..."
        )


        word_pdf_dir = (
            tmp
            / "word_pdf"
        )


        word_pdf_dir.mkdir(
            exist_ok=True
        )


        for index, word in enumerate(
            word_files,
            start=1
        ):

            current_step += 1


            suffix = Path(
                word.name
            ).suffix.lower()


            word_path = (
                tmp
                / (
                    f"word_"
                    f"{index:04d}"
                    f"{suffix}"
                )
            )


            word_bytes = (
                word.getvalue()
            )


            word_path.write_bytes(
                word_bytes
            )


            try:

                pdf_path = word_to_pdf(
                    word_path,
                    word_pdf_dir
                )


                detection = (
                    detect_doc_number_from_pdf(
                        pdf_path,
                        allow_ocr=False
                    )
                )


                doc_no = (
                    detection[
                        "doc_no"
                    ]
                )


                if not doc_no:

                    errors.append(
                        {
                            "TYPE": "WORD",
                            "DOCUMENT NO.": "-",
                            "FILE": word.name,
                            "ERROR": (
                                "Word 轉 PDF 後 "
                                "找不到 DOC 文件編號"
                            ),
                        }
                    )


                    status.write(
                        (
                            f"✗ WORD {word.name} "
                            "→ 找不到 DOC 編號"
                        )
                    )


                    continue


                if doc_no not in word_candidates:

                    word_candidates[
                        doc_no
                    ] = []


                word_candidates[
                    doc_no
                ].append(
                    {
                        "doc_no": doc_no,
                        "name": word.name,
                        "bytes": word_bytes,
                        "suffix": suffix,
                    }
                )


                status.write(
                    (
                        f"✓ WORD [{index}/{word_count}] "
                        f"{word.name} "
                        f"→ {doc_no}"
                    )
                )


            except Exception as e:

                errors.append(
                    {
                        "TYPE": "WORD",
                        "DOCUMENT NO.": "-",
                        "FILE": word.name,
                        "ERROR": str(e),
                    }
                )


                status.write(
                    (
                        f"✗ WORD {word.name} "
                        f"→ {e}"
                    )
                )


            finally:

                percent = int(
                    current_step
                    / total_steps
                    * 100
                )


                progress.progress(
                    percent,
                    text=(
                        f"ANALYZING "
                        f"{current_step}/{total_steps}"
                    ),
                )


        # ====================================================
        # WORD DUPLICATES
        # ====================================================

        for doc_no, items in (
            word_candidates.items()
        ):

            if len(items) > 1:

                file_names = ", ".join(
                    item["name"]
                    for item in items
                )


                errors.append(
                    {
                        "TYPE": "WORD",
                        "DOCUMENT NO.": doc_no,
                        "FILE": file_names,
                        "ERROR": (
                            "Word 文件編號重複，"
                            "此編號不會自動執行"
                        ),
                    }
                )


    # ========================================================
    # MATCH
    # ========================================================

    matches = []


    all_doc_numbers = (
        set(
            cover_candidates.keys()
        )
        |
        set(
            word_candidates.keys()
        )
    )


    for doc_no in sorted(
        all_doc_numbers
    ):

        covers = (
            cover_candidates.get(
                doc_no,
                []
            )
        )

        words = (
            word_candidates.get(
                doc_no,
                []
            )
        )


        # ----------------------------------------------------
        # Exactly one Word + one Cover = safe match
        # ----------------------------------------------------

        if (
            len(covers) == 1
            and
            len(words) == 1
        ):

            matches.append(
                {
                    "doc_no": doc_no,

                    "word_name":
                    words[0]["name"],

                    "word_bytes":
                    words[0]["bytes"],

                    "word_suffix":
                    words[0]["suffix"],

                    "cover_name":
                    covers[0]["name"],

                    "cover_bytes":
                    covers[0]["bytes"],

                    "cover_method":
                    covers[0]["method"],
                }
            )


        # ----------------------------------------------------
        # Missing cover
        # ----------------------------------------------------

        elif (
            len(words) == 1
            and
            len(covers) == 0
        ):

            errors.append(
                {
                    "TYPE": "MATCH",
                    "DOCUMENT NO.": doc_no,
                    "FILE": words[0]["name"],
                    "ERROR": (
                        "Word 找不到對應 Signed Cover"
                    ),
                }
            )


        # ----------------------------------------------------
        # Missing Word
        # ----------------------------------------------------

        elif (
            len(covers) == 1
            and
            len(words) == 0
        ):

            errors.append(
                {
                    "TYPE": "MATCH",
                    "DOCUMENT NO.": doc_no,
                    "FILE": covers[0]["name"],
                    "ERROR": (
                        "Signed Cover 找不到對應 Word"
                    ),
                }
            )


        # Duplicate cases already have errors above.
        # Add a matching ambiguity message too.
        elif (
            len(covers) > 1
            or
            len(words) > 1
        ):

            errors.append(
                {
                    "TYPE": "MATCH",
                    "DOCUMENT NO.": doc_no,
                    "FILE": "-",
                    "ERROR": (
                        "無法建立唯一配對，"
                        "此文件編號已排除執行"
                    ),
                }
            )


    # ========================================================
    # SAVE ANALYSIS
    # ========================================================

    st.session_state[
        "analysis_done"
    ] = True

    st.session_state[
        "analysis_matches"
    ] = matches

    st.session_state[
        "analysis_errors"
    ] = errors


    progress.progress(
        100,
        text="ANALYSIS COMPLETE"
    )


    status.update(
        label=(
            "ANALYSIS COMPLETE "
            f"| MATCHED: {len(matches)} "
            f"| ERRORS: {len(errors)}"
        ),
        state="complete",
        expanded=True,
    )


# ============================================================
# EXECUTE MATCHED ITEMS
# ============================================================

def execute_matched_items():

    matches = (
        st.session_state[
            "analysis_matches"
        ]
    )


    if not matches:

        st.error(
            "目前沒有可執行的吻合項目。"
        )

        return


    reset_results()


    total = len(
        matches
    )

    success = 0
    failed = 0

    result_files = []


    progress = st.progress(
        0,
        text="PROCESSING MATCHED ITEMS..."
    )


    status = st.status(
        "CREATING FINAL PDF...",
        expanded=True,
    )


    with tempfile.TemporaryDirectory() as tmp_dir:

        tmp = Path(
            tmp_dir
        )


        word_dir = (
            tmp
            / "word"
        )

        pdf_dir = (
            tmp
            / "pdf"
        )

        cover_dir = (
            tmp
            / "cover"
        )

        final_dir = (
            tmp
            / "final"
        )


        for folder in [
            word_dir,
            pdf_dir,
            cover_dir,
            final_dir
        ]:

            folder.mkdir(
                exist_ok=True
            )


        for index, item in enumerate(
            matches,
            start=1
        ):

            doc_no = (
                item[
                    "doc_no"
                ]
            )


            try:

                # --------------------------------------------
                # Restore Word
                # --------------------------------------------

                word_path = (
                    word_dir
                    / (
                        f"word_{index:04d}"
                        f"{item['word_suffix']}"
                    )
                )


                word_path.write_bytes(
                    item[
                        "word_bytes"
                    ]
                )


                # --------------------------------------------
                # Restore Cover
                # --------------------------------------------

                cover_path = (
                    cover_dir
                    / (
                        f"cover_{index:04d}.pdf"
                    )
                )


                cover_path.write_bytes(
                    item[
                        "cover_bytes"
                    ]
                )


                # --------------------------------------------
                # Word → PDF
                # --------------------------------------------

                main_pdf = (
                    word_to_pdf(
                        word_path,
                        pdf_dir
                    )
                )


                # --------------------------------------------
                # Final
                # --------------------------------------------

                final_name = (
                    f"{doc_no}-Final.pdf"
                )


                final_path = (
                    final_dir
                    / final_name
                )


                replace_first_page(
                    cover_path,
                    main_pdf,
                    final_path
                )


                result_files.append(
                    (
                        final_name,
                        final_path.read_bytes()
                    )
                )


                success += 1


                status.write(
                    (
                        f"✓ [{index}/{total}] "
                        f"{doc_no} "
                        f"→ {final_name}"
                    )
                )


            except Exception as e:

                failed += 1


                status.write(
                    (
                        f"✗ [{index}/{total}] "
                        f"{doc_no} "
                        f"→ {e}"
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
                    f"{index}/{total} "
                    f"({percent}%)"
                ),
            )


    # ========================================================
    # OUTPUT
    # ========================================================

    if result_files:

        timestamp = (
            datetime.now(
                TAIPEI_TZ
            ).strftime(
                "%Y%m%d_%H%M%S"
            )
        )


        zip_name = (
            f"PLM_PDF_"
            f"{timestamp}.zip"
        )


        zip_data = (
            create_zip(
                result_files
            )
        )


        st.session_state[
            "result_files"
        ] = result_files


        st.session_state[
            "result_zip"
        ] = zip_data


        st.session_state[
            "result_zip_name"
        ] = zip_name


    st.session_state[
        "last_success"
    ] = success

    st.session_state[
        "last_failed"
    ] = failed

    st.session_state[
        "last_total"
    ] = total


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
# SHOW ANALYSIS RESULT
# ============================================================

def show_analysis_result():

    if not st.session_state[
        "analysis_done"
    ]:

        return


    matches = (
        st.session_state[
            "analysis_matches"
        ]
    )

    errors = (
        st.session_state[
            "analysis_errors"
        ]
    )


    st.divider()


    st.subheader(
        "ANALYSIS RESULT"
    )


    c1, c2, c3 = st.columns(
        3
    )


    c1.metric(
        "MATCHED",
        len(matches)
    )


    c2.metric(
        "ERROR ITEMS",
        len(errors)
    )


    c3.metric(
        "EXECUTABLE",
        len(matches)
    )


    # ========================================================
    # MATCH TABLE
    # ========================================================

    if matches:

        st.markdown(
            "### ✓ MATCHED ITEMS"
        )


        matched_rows = []


        for item in matches:

            matched_rows.append(
                {
                    "DOCUMENT NO.":
                    item["doc_no"],

                    "WORD FILE":
                    item["word_name"],

                    "SIGNED COVER":
                    item["cover_name"],

                    "DETECTION":
                    item["cover_method"],

                    "STATUS":
                    "READY",
                }
            )


        st.dataframe(
            matched_rows,
            use_container_width=True,
            hide_index=True,
        )


    else:

        st.warning(
            "沒有找到可安全執行的一對一配對。"
        )


    # ========================================================
    # ERROR TABLE
    # ========================================================

    if errors:

        st.markdown(
            "### ⚠ ERROR / UNMATCHED ITEMS"
        )


        error_rows = []


        for error in errors:

            error_rows.append(
                {
                    "TYPE":
                    error.get(
                        "TYPE",
                        "-"
                    ),

                    "DOCUMENT NO.":
                    error.get(
                        "DOCUMENT NO.",
                        "-"
                    ),

                    "FILE":
                    error.get(
                        "FILE",
                        "-"
                    ),

                    "ERROR":
                    error.get(
                        "ERROR",
                        "-"
                    ),
                }
            )


        st.dataframe(
            error_rows,
            use_container_width=True,
            hide_index=True,
        )


        with st.expander(
            "ERROR DETAILS / OCR DEBUG"
        ):

            has_debug = False


            for error in errors:

                debug = error.get(
                    "DEBUG",
                    ""
                )


                if debug:

                    has_debug = True

                    st.markdown(
                        f"**{error.get('FILE', '-')}**"
                    )

                    st.code(
                        debug
                    )


            if not has_debug:

                st.write(
                    "目前沒有 OCR Debug 資訊。"
                )


    # ========================================================
    # EXECUTE BUTTON
    # ========================================================

    st.divider()


    if matches:

        st.info(
            (
                f"已確認 {len(matches)} 組可安全配對。"
                "錯誤項目將跳過，不影響吻合項目執行。"
            )
        )


        if st.button(
            (
                f"執行吻合項目 "
                f"({len(matches)})"
            ),
            type="primary",
            use_container_width=True,
            key="execute_matched",
        ):

            execute_matched_items()


    else:

        st.button(
            "執行吻合項目 (0)",
            disabled=True,
            use_container_width=True,
        )


# ============================================================
# DOWNLOAD RESULTS
# ============================================================

def show_download_results():

    if not st.session_state[
        "result_zip"
    ]:

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
        st.session_state[
            "last_total"
        ]
    )


    c2.metric(
        "SUCCESS",
        st.session_state[
            "last_success"
        ]
    )


    c3.metric(
        "FAILED",
        st.session_state[
            "last_failed"
        ]
    )


    st.download_button(
        label=(
            "DOWNLOAD ALL "
            f"({st.session_state['last_success']} FILES)"
        ),
        data=(
            st.session_state[
                "result_zip"
            ]
        ),
        file_name=(
            st.session_state[
                "result_zip_name"
            ]
        ),
        mime="application/zip",
        type="primary",
        use_container_width=True,
    )


    with st.expander(
        "INDIVIDUAL PDF DOWNLOADS"
    ):

        for index, (
            filename,
            data
        ) in enumerate(
            st.session_state[
                "result_files"
            ]
        ):

            st.download_button(
                label=filename,
                data=data,
                file_name=filename,
                mime="application/pdf",
                key=(
                    f"download_"
                    f"{index}_"
                    f"{filename}"
                ),
                use_container_width=True,
            )


# ============================================================
# MAIN APP
# ============================================================

def show_main_app():

    username = (
        st.session_state[
            "username"
        ]
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

            st.session_state[
                "authenticated"
            ] = False

            st.session_state[
                "username"
            ] = None

            reset_analysis()
            reset_results()

            st.rerun()


    st.divider()


    st.markdown(
        """
        ### AUTO PDF COVER MATCHING

        一次上傳 **Word 文件 + Signed Cover PDF**。

        Signed Cover 檔名可以任意。

        系統會從文件內容辨識：

        `DOC-YYYY-NNNNN`

        Signed Cover 若為掃描 PDF，會自動 OCR。

        **無法配對的項目只會列為 Error，不會阻止其他已吻合項目執行。**
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
        key="main_upload",
    )


    if uploaded_files:

        word_files, pdf_files = (
            analyze_uploaded_files(
                uploaded_files
            )
        )


        # ====================================================
        # FILE COUNT
        # ====================================================

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
            ==
            len(pdf_files)
        )


        c3.metric(
            "COUNT CHECK",
            (
                "OK"
                if count_ok
                else "WARNING"
            )
        )


        if not count_ok:

            st.warning(
                (
                    "Word 與 Signed Cover 數量不一致。"
                    "仍可進行分析，已吻合的項目仍可執行。 "
                    f"Word = {len(word_files)} / "
                    f"Signed Cover = {len(pdf_files)}"
                )
            )


        # ====================================================
        # ANALYZE BUTTON
        # ====================================================

        if st.button(
            "ANALYZE FILES",
            type="primary",
            use_container_width=True,
        ):

            run_analysis(
                uploaded_files
            )


    # ========================================================
    # ANALYSIS RESULT
    # ========================================================

    show_analysis_result()


    # ========================================================
    # DOWNLOAD
    # ========================================================

    show_download_results()


# ============================================================
# START
# ============================================================

if not st.session_state[
    "authenticated"
]:

    show_login()

else:

    show_main_app()
