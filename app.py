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
APP_VERSION = "1.6.0"

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
