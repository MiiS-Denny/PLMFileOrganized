import io
import hmac
import time
import smtplib
import hashlib
import secrets
import zipfile
import subprocess
import tempfile

from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter


# ============================================================
# SYSTEM CONFIG
# ============================================================

APP_NAME = "PLM PDF Automation Tool"
APP_VERSION = "1.1.0"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

OTP_EXPIRE_MINUTES = 5
OTP_RESEND_SECONDS = 60
OTP_MAX_ATTEMPTS = 5


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

    .login-info {
        font-family: monospace;
        font-size: 13px;
        color: #666;
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

    "otp_sent": False,
    "otp_code": None,
    "otp_expire_at": None,
    "otp_last_sent_at": None,
    "otp_attempts": 0,
    "otp_account": None,
    "otp_email": None,

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
# USERS
# ============================================================

def get_user_db():

    try:

        users = st.secrets["users"]

        result = {}

        for username, data in users.items():

            result[username] = {
                "email": str(data["email"]).strip().lower(),
                "algo": str(data["algo"]),
                "iter": int(data["iter"]),
                "salt": str(data["salt"]),
                "hash": str(data["hash"]),
            }

        return result

    except Exception as e:

        st.error(
            "無法讀取使用者設定。"
        )

        st.code(str(e))

        st.stop()


USER_DB = get_user_db()


# ============================================================
# PASSWORD VERIFY
# ============================================================

def verify_password(
    username,
    password
):

    if username not in USER_DB:
        return False

    info = USER_DB[username]

    if info["algo"] != "pbkdf2_sha256":
        return False

    try:

        salt = bytes.fromhex(
            info["salt"]
        )

        expected = bytes.fromhex(
            info["hash"]
        )

        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(info["iter"]),
        )

        return hmac.compare_digest(
            calculated,
            expected,
        )

    except Exception:

        return False


# ============================================================
# EMAIL MASK
# ============================================================

def mask_email(email):

    try:

        account, domain = email.split(
            "@",
            1
        )

        if len(account) <= 2:
            masked = account[0] + "*"
        else:
            masked = (
                account[:2]
                + "*" * max(
                    len(account) - 2,
                    2
                )
            )

        return (
            masked
            + "@"
            + domain
        )

    except:

        return email


# ============================================================
# OTP
# ============================================================

def generate_otp():

    return f"{secrets.randbelow(1000000):06d}"


# ============================================================
# SMTP CONFIG
# ============================================================

def get_email_config():

    try:

        config = st.secrets["email"]

        return {
            "smtp_host": str(
                config["smtp_host"]
            ),
            "smtp_port": int(
                config["smtp_port"]
            ),
            "sender_email": str(
                config["sender_email"]
            ),
            "sender_password": str(
                config["sender_password"]
            ),
            "use_tls": bool(
                config.get(
                    "use_tls",
                    True
                )
            ),
        }

    except Exception as e:

        raise RuntimeError(
            f"SMTP Secrets 設定錯誤：{e}"
        )


# ============================================================
# SEND OTP
# ============================================================

def send_otp_email(
    username,
    target_email,
    otp
):

    cfg = get_email_config()

    subject = (
        "PLM PDF Automation - Verification Code"
    )

    body = f"""
Hi {username},

Your verification code is:

{otp}

This code will expire in {OTP_EXPIRE_MINUTES} minutes.

If you did not request this code, please ignore this email.

PLM PDF Automation Tool
Version {APP_VERSION}
"""


    msg = MIMEMultipart()

    msg["From"] = cfg[
        "sender_email"
    ]

    msg["To"] = target_email

    msg["Subject"] = subject


    msg.attach(
        MIMEText(
            body,
            "plain",
            "utf-8"
        )
    )


    server = smtplib.SMTP(
        cfg["smtp_host"],
        cfg["smtp_port"],
        timeout=20,
    )


    try:

        server.ehlo()

        if cfg["use_tls"]:

            server.starttls()

            server.ehlo()


        server.login(
            cfg["sender_email"],
            cfg["sender_password"],
        )


        server.sendmail(
            cfg["sender_email"],
            [target_email],
            msg.as_string(),
        )


    finally:

        try:
            server.quit()
        except:
            pass


# ============================================================
# RESET OTP
# ============================================================

def reset_otp():

    st.session_state.otp_sent = False

    st.session_state.otp_code = None

    st.session_state.otp_expire_at = None

    st.session_state.otp_attempts = 0

    st.session_state.otp_account = None

    st.session_state.otp_email = None


# ============================================================
# SUPABASE CONFIG
# ============================================================

def get_supabase_config():

    try:

        url = str(
            st.secrets["supabase"]["url"]
        ).rstrip("/")

        key = str(
            st.secrets[
                "supabase"
            ][
                "service_role_key"
            ]
        )

        return url, key

    except:

        return None, None


# ============================================================
# WRITE LOGIN LOG
# ============================================================

def write_login_log(
    username,
    email,
    success,
):

    url, key = get_supabase_config()

    if not url or not key:
        return


    payload = {
        "username": username,
        "email": email,
        "login_time": datetime.now(
            TAIPEI_TZ
        ).isoformat(),
        "otp_verified": bool(
            success
        ),
        "app_version": APP_VERSION,
    }


    headers = {
        "apikey": key,
        "Authorization": (
            f"Bearer {key}"
        ),
        "Content-Type": (
            "application/json"
        ),
        "Prefer": "return=minimal",
    }


    try:

        requests.post(
            (
                f"{url}"
                "/rest/v1/login_log"
            ),
            headers=headers,
            json=payload,
            timeout=10,
        )

    except:
        pass


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

        return (
            False,
            "Supabase 尚未設定"
        )


    duration = (
        finished_at
        - started_at
    ).total_seconds()


    payload = {
        "username": username,

        "started_at": (
            started_at.isoformat()
        ),

        "finished_at": (
            finished_at.isoformat()
        ),

        "file_count": int(
            file_count
        ),

        "success_count": int(
            success_count
        ),

        "failed_count": int(
            failed_count
        ),

        "duration_seconds": round(
            duration,
            2
        ),

        "app_version": APP_VERSION,
    }


    headers = {
        "apikey": key,
        "Authorization": (
            f"Bearer {key}"
        ),
        "Content-Type": (
            "application/json"
        ),
        "Prefer": "return=minimal",
    }


    try:

        response = requests.post(
            (
                f"{url}"
                "/rest/v1/usage_log"
            ),
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
            (
                f"HTTP "
                f"{response.status_code}: "
                f"{response.text}"
            )
        )


    except Exception as e:

        return (
            False,
            str(e)
        )


# ============================================================
# READ USAGE LOG
# ============================================================

def read_usage_logs(
    limit=1000
):

    url, key = get_supabase_config()

    if not url or not key:
        return []


    headers = {
        "apikey": key,

        "Authorization": (
            f"Bearer {key}"
        ),
    }


    params = {
        "select": "*",
        "order": "started_at.desc",
        "limit": limit,
    }


    try:

        response = requests.get(
            (
                f"{url}"
                "/rest/v1/usage_log"
            ),
            headers=headers,
            params=params,
            timeout=15,
        )


        if response.status_code == 200:

            return response.json()


    except:
        pass


    return []


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
        / (
            Path(
                word_path
            ).stem
            + ".pdf"
        )
    )


    if not expected_pdf.exists():

        raise RuntimeError(
            "Word → PDF 失敗\n"
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
            "Cover PDF 必須只有 1 頁"
        )


    if len(
        main_reader.pages
    ) < 1:

        raise RuntimeError(
            "Word PDF 沒有頁面"
        )


    writer = PdfWriter()


    writer.add_page(
        cover_reader.pages[0]
    )


    for page_index in range(
        1,
        len(
            main_reader.pages
        )
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
        [1.2, 1, 1.2]
    )


    with center:

        st.subheader(
            "AUTHENTICATION"
        )


        username = st.selectbox(
            "Account",
            options=sorted(
                USER_DB.keys()
            ),
            index=None,
            placeholder=(
                "Select account"
            ),
        )


        email = st.text_input(
            "E-mail",
            placeholder=(
                "name@company.com"
            ),
        )


        password = st.text_input(
            "Password",
            type="password",
        )


        st.caption(
            "帳號、Email、密碼皆正確後，"
            "系統才會寄送 6 碼驗證碼。"
        )


        # ====================================================
        # SEND OTP
        # ====================================================

        if st.button(
            "SEND VERIFICATION CODE",
            type="primary",
            use_container_width=True,
        ):

            if not username:

                st.warning(
                    "請選擇帳號"
                )


            elif not email:

                st.warning(
                    "請輸入 Email"
                )


            elif not password:

                st.warning(
                    "請輸入密碼"
                )


            else:

                expected_email = (
                    USER_DB[
                        username
                    ][
                        "email"
                    ]
                )


                entered_email = (
                    email
                    .strip()
                    .lower()
                )


                if entered_email != expected_email:

                    st.error(
                        "帳號、Email 或密碼錯誤"
                    )


                elif not verify_password(
                    username,
                    password
                ):

                    st.error(
                        "帳號、Email 或密碼錯誤"
                    )


                else:

                    now = datetime.now(
                        TAIPEI_TZ
                    )


                    last_sent = (
                        st.session_state
                        .otp_last_sent_at
                    )


                    if last_sent:

                        elapsed = (
                            now
                            - last_sent
                        ).total_seconds()


                        if (
                            elapsed
                            < OTP_RESEND_SECONDS
                        ):

                            remaining = int(
                                OTP_RESEND_SECONDS
                                - elapsed
                            )


                            st.warning(
                                f"請等待 "
                                f"{remaining} 秒後"
                                "再重新寄送。"
                            )

                            st.stop()


                    otp = generate_otp()


                    try:

                        send_otp_email(
                            username,
                            expected_email,
                            otp,
                        )


                        st.session_state.otp_sent = True

                        st.session_state.otp_code = otp

                        st.session_state.otp_expire_at = (
                            now
                            + timedelta(
                                minutes=(
                                    OTP_EXPIRE_MINUTES
                                )
                            )
                        )

                        st.session_state.otp_last_sent_at = now

                        st.session_state.otp_attempts = 0

                        st.session_state.otp_account = username

                        st.session_state.otp_email = expected_email


                        st.success(
                            "驗證碼已寄送至 "
                            + mask_email(
                                expected_email
                            )
                        )


                    except Exception as e:

                        st.error(
                            f"寄送失敗：{e}"
                        )


        # ====================================================
        # OTP AREA
        # ====================================================

        if st.session_state.otp_sent:

            st.divider()

            st.markdown(
                "**Verification Code**"
            )


            st.caption(
                "驗證碼有效時間 "
                f"{OTP_EXPIRE_MINUTES} 分鐘"
            )


            otp_input = st.text_input(
                "6-digit code",
                max_chars=6,
                placeholder="000000",
            )


            if st.button(
                "VERIFY & LOGIN",
                type="primary",
                use_container_width=True,
            ):

                now = datetime.now(
                    TAIPEI_TZ
                )


                if (
                    st.session_state
                    .otp_account
                    != username
                ):

                    st.error(
                        "帳號已變更，"
                        "請重新取得驗證碼。"
                    )

                    reset_otp()

                    st.stop()


                if (
                    st.session_state
                    .otp_email
                    != email
                    .strip()
                    .lower()
                ):

                    st.error(
                        "Email 已變更，"
                        "請重新取得驗證碼。"
                    )

                    reset_otp()

                    st.stop()


                if (
                    not verify_password(
                        username,
                        password
                    )
                ):

                    st.error(
                        "密碼錯誤，"
                        "請重新取得驗證碼。"
                    )

                    reset_otp()

                    st.stop()


                if (
                    now
                    > st.session_state
                    .otp_expire_at
                ):

                    st.error(
                        "驗證碼已過期，"
                        "請重新寄送。"
                    )

                    reset_otp()

                    st.stop()


                if (
                    st.session_state
                    .otp_attempts
                    >= OTP_MAX_ATTEMPTS
                ):

                    st.error(
                        "驗證次數已超過限制，"
                        "請重新取得驗證碼。"
                    )

                    reset_otp()

                    st.stop()


                st.session_state.otp_attempts += 1


                if not hmac.compare_digest(
                    otp_input.strip(),
                    st.session_state
                    .otp_code
                ):

                    remaining = (
                        OTP_MAX_ATTEMPTS
                        - st.session_state
                        .otp_attempts
                    )


                    st.error(
                        "驗證碼錯誤。"
                        f"剩餘 {remaining} 次。"
                    )

                    st.stop()


                # ============================================
                # LOGIN OK
                # ============================================

                st.session_state.authenticated = True

                st.session_state.username = username


                write_login_log(
                    username=username,
                    email=(
                        USER_DB[
                            username
                        ][
                            "email"
                        ]
                    ),
                    success=True,
                )


                reset_otp()

                st.rerun()


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
            "尚無使用紀錄，或 Supabase 未連線。"
        )

        return


    df = pd.DataFrame(
        logs
    )


    if "started_at" in df.columns:

        df[
            "started_at"
        ] = pd.to_datetime(
            df["started_at"],
            errors="coerce",
            utc=True,
        ).dt.tz_convert(
            "Asia/Taipei"
        )


    # ========================================================
    # SUMMARY
    # ========================================================

    if not df.empty:

        summary = (
            df
            .groupby(
                "username"
            )
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
                "username":
                "使用者"
            },
            inplace=True
        )


        st.markdown(
            "#### User Summary"
        )


        st.dataframe(
            summary,
            use_container_width=True,
            hide_index=True,
        )


        st.markdown(
            "#### Recent Activity"
        )


        columns = [
            c
            for c in [
                "username",
                "started_at",
                "file_count",
                "success_count",
                "failed_count",
                "duration_seconds",
                "app_version",
            ]
            if c in df.columns
        ]


        st.dataframe(
            df[columns],
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# RUN PROCESS
# ============================================================

def run_process(
    username,
    uploaded_files,
):

    started_at = datetime.now(
        TAIPEI_TZ
    )


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
        if Path(
            f.name
        ).suffix.lower()
        == ".pdf"
        and f.name.lower().startswith(
            "cover-"
        )
    ]


    cover_map = {
        f.name.lower(): f
        for f in cover_files
    }


    total = len(
        word_files
    )


    success = 0
    failed = 0

    result_files = []


    progress = st.progress(
        0,
        text=(
            "System Initializing..."
        ),
    )


    status = st.status(
        "PROCESSING",
        expanded=True,
    )


    with tempfile.TemporaryDirectory() as tmp:

        tmp = Path(tmp)


        for index, word in enumerate(
            word_files,
            start=1,
        ):

            base = Path(
                word.name
            ).stem


            expected_cover = (
                f"cover-{base}.pdf"
            )


            status.write(
                f"[{index}/{total}] "
                f"{word.name}"
            )


            try:

                if (
                    expected_cover.lower()
                    not in cover_map
                ):

                    raise FileNotFoundError(
                        "Cover not found: "
                        + expected_cover
                    )


                # ============================================
                # SAVE WORD
                # ============================================

                word_path = (
                    tmp
                    / word.name
                )


                word_path.write_bytes(
                    word.getvalue()
                )


                # ============================================
                # SAVE COVER
                # ============================================

                cover_file = (
                    cover_map[
                        expected_cover
                        .lower()
                    ]
                )


                cover_path = (
                    tmp
                    / expected_cover
                )


                cover_path.write_bytes(
                    cover_file.getvalue()
                )


                # ============================================
                # WORD → PDF
                # ============================================

                status.write(
                    "→ Word → PDF"
                )


                main_pdf = word_to_pdf(
                    word_path,
                    tmp
                )


                # ============================================
                # REPLACE COVER
                # ============================================

                status.write(
                    "→ Replace Cover Page"
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


                result_files.append(
                    (
                        final_name,
                        final_path.read_bytes(),
                    )
                )


                success += 1


                status.write(
                    "✓ "
                    + final_name
                )


            except Exception as e:

                failed += 1


                status.write(
                    "✗ "
                    + word.name
                )


                status.write(
                    str(e)
                )


            percent = int(
                (
                    index
                    / total
                )
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
    # DATABASE LOG
    # ========================================================

    log_ok, log_error = write_usage_log(
        username=username,
        started_at=started_at,
        finished_at=finished_at,
        file_count=total,
        success_count=success,
        failed_count=failed,
    )


    status.update(
        label=(
            "COMPLETED "
            f"| Success: {success} "
            f"| Failed: {failed}"
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
            "PDF 已完成，但後台紀錄失敗："
            + str(
                log_error
            )
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
        "Total",
        st.session_state.last_total
    )


    c2.metric(
        "Success",
        st.session_state.last_success
    )


    c3.metric(
        "Failed",
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
            st.session_state
            .result_zip_name
        ),
        mime="application/zip",
        type="primary",
        use_container_width=True,
    )


    with st.expander(
        "Individual PDF Downloads"
    ):

        for filename, data in (
            st.session_state
            .result_files
        ):

            st.download_button(
                label=filename,
                data=data,
                file_name=filename,
                mime="application/pdf",
                key=(
                    "dl_"
                    + filename
                ),
            )


# ============================================================
# MAIN PAGE
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
            f"User: **{username}**"
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

            reset_otp()

            st.rerun()


    st.divider()


    st.markdown(
        """
        ### PDF Cover Replacement

        將所有 Word 與 Cover PDF 一次拖進下方。

        命名規則：

        `DOC-001.docx`

        對應：

        `cover-DOC-001.pdf`
        """
    )


    uploaded_files = st.file_uploader(
        "DROP WORD + COVER PDF FILES HERE",
        type=[
            "doc",
            "docx",
            "pdf"
        ],
        accept_multiple_files=True,
    )


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
            if Path(
                f.name
            ).suffix.lower()
            == ".pdf"
            and f.name.lower().startswith(
                "cover-"
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
                    "Word":
                    word.name,

                    "Expected Cover":
                    expected,

                    "Status":
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
            if row["Status"]
            == "MISSING"
        )


        c1, c2, c3 = st.columns(
            3
        )


        c1.metric(
            "Word",
            len(word_files)
        )


        c2.metric(
            "Cover",
            len(cover_files)
        )


        c3.metric(
            "Missing Cover",
            missing_count
        )


        start_disabled = (
            len(word_files) == 0
            or missing_count > 0
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


    show_download_results()


    # ========================================================
    # DENNY ADMIN
    # ========================================================

    if username == "Denny":

        st.divider()


        with st.expander(
            "ADMIN / Usage History"
        ):

            show_admin_dashboard()


# ============================================================
# START APP
# ============================================================

if not st.session_state.authenticated:

    show_login()

else:

    show_main_app()
