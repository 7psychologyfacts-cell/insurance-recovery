# =============================================================================
# INSURANCE RECOVERY — FLASK BACKEND (Vercel deployment)
# Ported from Colab pipeline. Same extraction/matching logic, but:
#   • Colab-browser Google auth  → Google Service Account (env var)
#   • Hardcoded secrets          → environment variables (with UI override)
#   • One giant script           → callable functions behind Flask routes
# =============================================================================

import os
import re
import io
import csv
import json
import time
import email
import email.policy
import imaplib
from email import message_from_bytes
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")

# #############################################################################
# SECTION 0 — DEFAULTS (env vars first, UI can override per-request)
# #############################################################################

DEFAULTS = {
    "geminiApiKey": os.environ.get("GEMINI_API_KEY", ""),
    "geminiModel": os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
    "sheetUrl": os.environ.get("SHEET_URL", ""),
    "worksheetName": os.environ.get("WORKSHEET_NAME", "Sheet1"),
    "fromDate": os.environ.get("FROM_DATE", ""),
    "toDate": os.environ.get("TO_DATE", ""),
    "imapHost": os.environ.get("IMAP_HOST", ""),
    "imapPort": int(os.environ.get("IMAP_PORT", "993")),
    "imapUser": os.environ.get("IMAP_USER", ""),
    "imapPass": os.environ.get("IMAP_PASS", ""),
    "folders": [
        "INBOX/Jaipur", "INBOX/Indore", "INBOX/Krishna", "INBOX/SG",
        "INBOX/Mohali", "INBOX/Vapi", "INBOX/jabalpur", "INBOX/Surat",
        "INBOX/Naroda",
    ],
}

GEMINI_CALL_GAP_SECONDS = 3
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_BACKOFF = 10
GEMINI_RETRY_BACKOFF_CAP = 90

BATCH_SIZE = 5
BATCH_COOLDOWN_SECONDS = 15

TMP_DIR = "/tmp/fetched_emails"


def cfg_from_request(body: dict) -> dict:
    """Frontend se aayi settings ko defaults/env ke saath merge karta hai."""
    cfg = dict(DEFAULTS)
    for key in cfg:
        if body.get(key) not in (None, "", []):
            cfg[key] = body[key]
    cfg["subjectBlacklist"] = [
        k.strip().lower() for k in (body.get("subjectBlacklist") or "").splitlines() if k.strip()
    ]
    cfg["senderBlacklist"] = [
        k.strip().lower() for k in (body.get("senderBlacklist") or "").splitlines() if k.strip()
    ]
    return cfg


# #############################################################################
# SECTION 1 — GEMINI CLIENT (per-request, key aa sakta hai UI ya env se)
# #############################################################################

def get_gemini_client(api_key: str):
    from google import genai
    if not api_key:
        raise RuntimeError("Gemini API key nahi mili — settings me daalein ya GEMINI_API_KEY env set karein.")
    return genai.Client(api_key=api_key)


def get_generation_config():
    from google.genai import types as genai_types
    return genai_types.GenerateContentConfig(
        max_output_tokens=8192,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    )


def safe_generate_content(client, model: str, prompt: str, config=None):
    wait = GEMINI_RETRY_BACKOFF
    last_err = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            if config is not None:
                response = client.models.generate_content(model=model, contents=prompt, config=config)
            else:
                response = client.models.generate_content(model=model, contents=prompt)
            time.sleep(GEMINI_CALL_GAP_SECONDS)
            return response
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            is_rate_issue = ("429" in msg or "quota" in msg or "rate" in msg or "resource_exhausted" in msg)
            if is_rate_issue and attempt < GEMINI_MAX_RETRIES:
                time.sleep(wait)
                wait = min(wait * 2, GEMINI_RETRY_BACKOFF_CAP)
                continue
            time.sleep(GEMINI_CALL_GAP_SECONDS)
            raise last_err
    raise last_err


# #############################################################################
# SECTION 2 — CORE EMAIL EXTRACTION (HTML, regex, Gemini) — same logic as Colab
# #############################################################################

OUTPUT_COLUMNS = [
    "ClaimNumber", "PatientName", "InsuranceCompany",
    "Approved", "Settlement", "Outstanding", "Deduction", "Remarks",
]

HEADER_SYNONYMS = {
    "ClaimNumber": ["claim id", "claim no", "claim number", "claim_no", "claimno", "Claim No."],
    "AdmissionNo": ["admission no", "admission_no", "adm no"],
    "PatientName": ["patient name", "name of patient", "patient", "name", "Customer Name"],
    "InsuranceCompany": [
        "tpa name", "insurance co", "insurance company", "sponsor name",
        "insurer", "tpa", "Sponsor Name - Insurance/Corporate", "Name of Company", "Name of Insurance",
    ],
    "Approved": [
        "sponsor approved amt", "approved amount", "approved amt",
        "sanctioned amount", "approved", "final approved amount",
    ],
    "Settlement": [
        "amount received", "settlement", "amount paid", "paid amount",
        "settled amount", "settled amt", "amount settled",
    ],
    "Outstanding": [
        "net outstanding", "out standing", "outstanding amt", "sponsor approved amt",
        "outstanding amount", "outstanding", "balance due", "net bill amt",
        "Net Bill Amt (Formula)", "Final AL Amount",
    ],
    "Deduction": [
        "deduction", "disallowed", "short paid",
        "deducation", "deduction amount", "deduction amt", "difference",
    ],
    "Remarks": ["remarks", "deduction remarks", "claim status", "status"],
}


def read_eml(file_path: str) -> dict:
    with open(file_path, "rb") as f:
        raw = f.read()
    msg = message_from_bytes(raw, policy=email.policy.default)

    subject = msg.get("Subject", "").strip()
    date_str = msg.get("Date", "").strip()
    sender = msg.get("From", "").strip()
    to_field = msg.get("To", "").strip()

    try:
        date_dt = parsedate_to_datetime(date_str)
    except Exception:
        date_dt = None

    plain_parts, html_parts = [], []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if "attachment" in str(part.get("Content-Disposition", "")):
                continue
            if ct == "text/plain":
                try:
                    plain_parts.append(part.get_content())
                except Exception:
                    plain_parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
            elif ct == "text/html":
                try:
                    html_parts.append(part.get_content())
                except Exception:
                    html_parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
    else:
        try:
            plain_parts.append(msg.get_content())
        except Exception:
            plain_parts.append(msg.get_payload(decode=True).decode("utf-8", errors="replace"))

    plain_body = "\n\n".join(plain_parts).strip() if plain_parts else ""
    html_body = "\n\n".join(html_parts).strip() if html_parts else ""

    header = f"Subject: {subject}\nFrom: {sender}\nTo: {to_field}\nDate: {date_str}\n\n"

    return {
        "subject": subject,
        "date": date_dt,
        "date_str": date_str,
        "from": sender,
        "plain_text": plain_body,
        "html_raw": html_body,
        "body_text": header + (plain_body or _html_to_text(html_body)),
        "file_path": file_path,
    }


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    clean = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    clean = re.sub(r"</tr>|</p>|</div>", "\n", clean, flags=re.IGNORECASE)
    clean = re.sub(r"</td>|</th>", " | ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", "", clean)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&rsquo;", "'"), ("&#39;", "'")]:
        clean = clean.replace(ent, rep)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _clean_cell(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                      ("&rsquo;", "'"), ("&#39;", "'"), ("&quot;", '"')]:
        text = text.replace(ent, rep)
    text = re.sub(r"\s+", " ", text).strip()
    return text


SIGN_OFF_PATTERNS = [
    r'^\s*thanks\s*(?:&|and)?\s*regards[.!,\s]*$',
    r'^\s*thank\s+you[.!,\s]*$',
    r'^\s*sincerely[.!,\s]*$',
    r'^\s*best\s+regards[.!,\s]*$',
    r'^\s*warm\s+regards[.!,\s]*$',
    r'^\s*regards[.!,\s]*$',
    r'^\s*with\s+kindest?\s+regards[.!,\s]*$',
    r'^\s*kind\s+regards[.!,\s]*$',
    r'^\s*thanks\s*&?\s*$',
    r'^\s*best\s+wishes[.!,\s]*$',
    r'^\s*warm\s+wishes[.!,\s]*$',
    r'^\s*with\s+kind\s+regards[.!,\s]*$',
    r'^\s*with\s+warm\s+regards[.!,\s]*$',
    r'^\s*with\s+best\s+regards[.!,\s]*$',
    r'^\s*yours\s+faithfully[.!,\s]*$',
    r'^\s*yours\s+sincerely[.!,\s]*$',
    r'^\s*faithfully\s+yours[.!,\s]*$',
    r'^\s*respectfully[.!,\s]*$',
    r'^\s*thanks[.!?,\s]*$',
]


def get_latest_message(plain_text: str) -> str:
    if not plain_text:
        return ""
    old_separators = [
        r"^-----Original Message-----",
        r"^----- Forwarded Message -----",
        r"^From:.*\nSent:",
        r"^From:.*\nDate:",
        r"^On .*wrote:",
        r"^_{10,}\s*\n\s*From:",
        r"^_{10,}\s*\n",
        r"^_{10,}",
        r"^\s*From:.*\nTo:.*\nSent:",
        r"^\s*From:.*\nTo:.*\nDate:",
        r"^\s*________________________________",
        r"^\s*>.*From:",
        r"^\s*On .* at .* wrote:",
    ]
    all_patterns = old_separators + SIGN_OFF_PATTERNS
    combined = "|".join(all_patterns)
    parts = re.split(combined, plain_text, maxsplit=1, flags=re.MULTILINE | re.IGNORECASE)
    return parts[0].strip()


def find_html_quote_boundary(html: str) -> int:
    patterns = [
        r'<hr[^>]*>',
        r'class="?gmail_quote"?',
        r'id="?divRplyFwdMsg"?',
        r'class="?OutlookMessageHeader"?',
        r'-----Original Message-----',
        r'_{10,}',
        r'From:.*?(?:Sent|Date):.*?To:',
        r'(?:<br\s*/?>|</p>|</div>|>)\s*(?:thanks\s*(?:&(?:amp;)?\s*)?and\s*(?:&(?:amp;)?\s*)?regards|thank\s+you|sincerely|best\s+regards|warm\s+regards|with\s+kindest?\s+regards|kind\s+regards|regards)\b',
    ]
    min_pos = len(html)
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE | re.DOTALL):
            pos = m.start()
            if pos < min_pos:
                min_pos = pos
    return min_pos if min_pos < len(html) else -1


def get_latest_html(html: str) -> str:
    if not html:
        return ""
    boundary = find_html_quote_boundary(html)
    if boundary != -1:
        return html[:boundary].strip()
    return html


def build_latest_body_text(meta: dict) -> str:
    latest_plain = get_latest_message(meta.get("plain_text", ""))
    latest_html_text = _html_to_text(get_latest_html(meta.get("html_raw", "")))
    if latest_html_text and latest_html_text not in latest_plain:
        body = latest_plain + "\n\n" + latest_html_text
    else:
        body = latest_plain or latest_html_text
    header = f"Subject: {meta['subject']}\nFrom: {meta['from']}\nTo: (see original)\nDate: {meta['date_str']}\n\n"
    return header + body


def extract_html_tables(html: str) -> list:
    if not html:
        return []
    tables_raw = re.findall(r"<table.*?</table>", html, re.DOTALL | re.IGNORECASE)
    all_tables = []
    for t in tables_raw:
        rows_raw = re.findall(r"<tr.*?</tr>", t, re.DOTALL | re.IGNORECASE)
        rows = []
        for r in rows_raw:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.DOTALL | re.IGNORECASE)
            clean_cells = [_clean_cell(c) for c in cells]
            if any(clean_cells):
                rows.append(clean_cells)
        if len(rows) >= 2:
            all_tables.append(rows)
    return all_tables


def map_header_to_fields(header_row: list) -> dict:
    mapping = {}
    used_fields = set()
    for idx, raw_header in enumerate(header_row):
        h = raw_header.lower().strip()
        h = re.sub(r"[^\w\s]", "", h)
        h = re.sub(r"\s+", " ", h).strip()
        best_field, best_len = None, 0
        for field, synonyms in HEADER_SYNONYMS.items():
            if field in used_fields:
                continue
            for syn in synonyms:
                if syn in h and len(syn) > best_len:
                    best_field, best_len = field, len(syn)
        if best_field:
            mapping[idx] = best_field
            used_fields.add(best_field)
    return mapping


def parse_html_table_rows(table: list) -> list:
    if len(table) < 2:
        return []
    header_row = table[0]
    col_map = map_header_to_fields(header_row)

    has_amount_col = any(f in col_map.values() for f in ("Outstanding", "Approved", "Settlement", "Deduction"))
    if not has_amount_col:
        return []
    has_identity_col = any(f in col_map.values() for f in ("PatientName", "ClaimNumber", "AdmissionNo"))
    if not has_identity_col:
        return []

    claims = []
    for row in table[1:]:
        record = {col: "" for col in OUTPUT_COLUMNS}
        for idx, field in col_map.items():
            if idx < len(row):
                val = row[idx]
                if field in ("Approved", "Settlement", "Outstanding", "Deduction"):
                    val = _clean_amt(val)
                record[field] = val

        amt_present = any(
            record.get(f, "").replace(".", "").isdigit() for f in ("Approved", "Settlement", "Outstanding")
        )
        if not amt_present:
            continue

        if not record["Deduction"] and record["Approved"] and record["Settlement"]:
            try:
                record["Deduction"] = str(int(float(record["Approved"])) - int(float(record["Settlement"])))
            except ValueError:
                pass

        claims.append(record)
    return claims


def html_parse_claims(meta: dict) -> list:
    latest_html = get_latest_html(meta.get("html_raw", ""))
    if not latest_html:
        return []
    tables = extract_html_tables(latest_html)
    if not tables:
        return []
    for table in tables:
        claims = parse_html_table_rows(table)
        if claims:
            return claims
    return []


def _clean_amt(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[₹Rs,\-\u00A0\u2000-\u200F\u202F\uFEFF]", "", s)
    s = re.sub(r"^\(|\)$", "", s)
    s = re.sub(r"[^\d.]", "", s)
    parts = s.split(".")
    if len(parts) > 2:
        s = parts[0] + "." + "".join(parts[1:])
    return s


def regex_parse_plaintext(plain_text: str) -> list:
    marker = "Deduction Remarks"
    idx = plain_text.find(marker)
    if idx == -1:
        return []

    body = plain_text[idx + len(marker):]
    tokens = [t.strip() for t in re.split(r"\r?\n(?:\s*\r?\n)+", body) if t.strip()]

    adm_pat = re.compile(r"^ADSHL\d+$", re.IGNORECASE)
    claim_pat = re.compile(r"^[A-Z0-9]{7,}$")

    claims = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        is_claim_no = bool(claim_pat.match(tok))
        next_is_adm = (i + 1 < len(tokens) and adm_pat.match(tokens[i + 1]))

        if is_claim_no and next_is_adm and i + 9 < len(tokens):
            approved = _clean_amt(tokens[i + 6])
            settlement = _clean_amt(tokens[i + 7])
            outstanding = _clean_amt(tokens[i + 8])
            remarks = tokens[i + 10] if i + 10 < len(tokens) else ""

            if approved.isdigit() and settlement.isdigit() and outstanding.isdigit():
                claims.append({
                    "ClaimNumber": tokens[i],
                    "PatientName": tokens[i + 2],
                    "InsuranceCompany": tokens[i + 5],
                    "Approved": approved,
                    "Settlement": settlement,
                    "Outstanding": outstanding,
                    "Deduction": str(int(approved) - int(settlement)),
                    "Remarks": remarks[:300],
                })
                i += 11
                continue
        i += 1
    return claims


GEMINI_PROMPT = """You are a Hospital Insurance Recovery Expert.

Below is a document (email or PDF). Extract ALL claim records present.

CRITICAL WARNING: The plain text below may have formatting issues where
table columns got merged together without spaces (e.g. a settlement amount
and the next row's claim ID stuck together as one long number). If you see
suspiciously long digit strings (more than 10-12 consecutive digits) that
don't match a normal amount, DO NOT guess a split — instead, look for the
pattern across multiple rows to infer correct boundaries, or report the
field as uncertain by using empty string "" rather than guessing wrong.
Cross-check each extracted amount against the document's own stated totals
or amounts mentioned in the email body, if such totals exist.

For EACH claim:
- "ClaimNumber"      : Claim ID/No (string, "" if not found)
- "PatientName"      : Patient full name ("" if not found)
- "InsuranceCompany" : Insurance company or TPA name ("" if not found)
- "Approved"         : Total approved/sanctioned amount — plain digits, no commas/symbols
- "Settlement"       : Amount paid/settled — plain digits ("" if not found)
- "Outstanding"      : Balance/short-paid/pending amount — plain digits
- "Deduction"        : Total deduction amount — plain digits
- "Remarks"          : Short reason (max 200 chars)

RULES:
- Extract EVERY claim — do NOT skip any.
- Amounts: plain integers only, e.g. "12162" not "12,162/-"
- If unsure about a merged/corrupted value, use "" rather than a wrong guess.
- Return ONLY valid JSON — no markdown, no explanation.
- "detected_format": one line describing the document type.

JSON format:
{{"detected_format":"...",
  "headers":["ClaimNumber","PatientName","InsuranceCompany","Approved","Settlement","Outstanding","Deduction","Remarks"],
  "rows":[{{"ClaimNumber":"","PatientName":"","InsuranceCompany":"","Approved":"","Settlement":"","Outstanding":"","Deduction":"","Remarks":""}}]
}}

---- DOCUMENT START ----
{body_text}
---- DOCUMENT END ----
"""


def gemini_parse_claims(meta: dict, client, model: str) -> list:
    latest_body = build_latest_body_text(meta)
    prompt = GEMINI_PROMPT.format(body_text=latest_body)
    try:
        response = safe_generate_content(client, model, prompt, config=get_generation_config())
    except Exception as e:
        print(f"        Gemini API error: {e}")
        return []

    text = (response.text or "").strip()
    if not text or "didn't come through" in text.lower():
        return []

    text_clean = re.sub(r"```(?:json)?\s*\n?|```", "", text).strip()
    try:
        parsed = json.loads(text_clean)
    except json.JSONDecodeError:
        s, e = text_clean.find("{"), text_clean.rfind("}")
        if s != -1 and e != -1:
            try:
                parsed = json.loads(text_clean[s:e + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []

    rows = parsed.get("rows", [])
    return rows


def extract_claims(meta: dict, client, model: str, use_latest_only=True) -> pd.DataFrame:
    claims = []
    method = ""

    claims = html_parse_claims(meta)
    if claims:
        method = "HTML-TABLE"

    if not claims:
        plain = get_latest_message(meta.get("plain_text", "")) if use_latest_only else meta.get("plain_text", "")
        if plain and "Deduction Remarks" in plain:
            claims = regex_parse_plaintext(plain)
            if claims:
                method = "PLAIN-TEXT-REGEX"

    if not claims:
        raw_rows = gemini_parse_claims(meta, client, model)
        for row in raw_rows:
            claims.append({col: row.get(col, "") for col in OUTPUT_COLUMNS})
        method = "GEMINI"

    if not claims:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df = pd.DataFrame(claims, columns=OUTPUT_COLUMNS)
    df["_source_file"] = meta["file_path"].split("/")[-1]
    df["_email_subject"] = meta["subject"]
    df["_email_date"] = meta["date_str"]
    df["_extraction_method"] = method
    return df


def to_float(val) -> float:
    s = _clean_amt(val)
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def calc_totals(df: pd.DataFrame) -> dict:
    return {
        "Approved": df["Approved"].apply(to_float).sum(),
        "Settlement": df["Settlement"].apply(to_float).sum(),
        "Outstanding": df["Outstanding"].apply(to_float).sum(),
        "Deduction": df["Deduction"].apply(to_float).sum(),
        "Cases": len(df),
    }


# #############################################################################
# SECTION 3 — UNIT / COMPANY MAPPING — same as Colab
# #############################################################################

SPECIAL_EMAILS = {"cpd@shalby.org": "SG"}

UNIT_PATTERNS = {
    ".indore@shalby.in": "Indore", ".indore@shalby.org": "Indore",
    ".jbp@shalby.in": "Jabalpur", ".jbp@shalby.org": "Jabalpur",
    ".jaipur@shalby.in": "Jaipur", ".jaipur@shalby.org": "Jaipur",
    ".krishna@shalby.in": "Krishna", ".krishna@shalby.org": "Krishna",
    ".sg@shalby.in": "SG", ".sg@shalby.org": "SG",
    ".vapi@shalby.in": "Vapi", ".vapi@shalby.org": "Vapi",
    ".naroda@shalby.in": "Naroda", ".naroda@shalby.org": "Naroda",
    ".surat@shalby.in": "Surat", ".surat@shalby.org": "Surat",
    ".mohali@shalby.in": "Mohali", ".mohali@shalby.org": "Mohali",
}


def get_unit_from_email(email_str):
    if not email_str:
        return "Unknown"
    email_str = email_str.lower().strip()
    if "<" in email_str and ">" in email_str:
        email_str = email_str[email_str.find("<") + 1:email_str.find(">")]
    if email_str in SPECIAL_EMAILS:
        return SPECIAL_EMAILS[email_str]
    for pattern, unit in UNIT_PATTERNS.items():
        if email_str.endswith(pattern):
            return unit
    return "Unknown"


DOMAIN_MAP = {
    "adityabirlahealth.com": "Aditya Birla Health Insurance",
    "adityabirlacapital.com": "Aditya Birla Health Insurance",
    "aai.aero": "Airport Authority Of India",
    "bajajallianz.co.in": "Bajaj Allianz General Insurance",
    "bajajgeneral.com": "Bajaj Allianz General Insurance",
    "careinsurance.com": "Care Health Insurance",
    "cisf.gov.in": "Central Industrial Security Force (CISF)",
    "cghs.nic.in": "CGHS - All Subsidiaries",
    "cholamsispl.com": "Chola MS General Insurance",
    "ipr.res.in": "CHSS - IPR",
    "isro.gov.in": "CHSS - ISRO",
    "echs.gov.in": "ECHS",
    "ericsontpa.com": "Ericson TPA",
    "fhpl.net": "Family Health Plan TPA",
    "generalicentral.com": "Generali Central Insurance",
    "geninsindia.com": "Genins India Insurance TPA",
    "ghpltpa.com": "Good Health Insurance TPA",
    "hdfcergo.com": "HDFC ERGO General Insurance",
    "safewaytpa.in": "Health Assist Insurance TPA",
    "healthassisttpa.com": "Health Assist Insurance TPA",
    "healthindiatpa.com": "Health India TPA",
    "hitpa.co.in": "Health Insurance TPA",
    "heritagehealthtpa.com": "Heritage Health TPA",
    "heritagehealthtpa.co.in": "Heritage Health TPA",
    "bajoria.in": "Heritage Health TPA",
    "icicilombard.com": "ICICI Lombard General Insurance",
    "iffcotokio.co.in": "Iffco Tokio General Insurance",
    "ext.iffcotokio.co.in": "Iffco Tokio General Insurance",
    "indusindinsurance.com": "Indusind General Insurance",
    "manipalcigna.com": "Manipal Cigna Health Insurance",
    "mdindia.com": "MDIndia Health Insurance TPA",
    "mediassist.in": "Medi Assist TPA",
    "medsave.in": "Medsave Health TPA",
    "navi.com": "Navi General Insurance",
    "nivabupa.com": "Niva Bupa Health Insurance",
    "ongc.co.in": "ONGC",
    "paramounttpa.com": "Paramount Health TPA",
    "rbi.org.in": "Reserve Bank of India",
    "rajasthan.gov.in": "RGHS",
    "gov.in": "RGHS",
    "royalsundaram.in": "Royal Sundaram Insurance",
    "sbigeneral.in": "SBI General Insurance",
    "starhealth.in": "Star Health & Allied Insurance",
    "starinsurance.in": "Star Health & Allied Insurance",
    "tataaig.com": "Tata AIG General Insurance",
    "anupengg.com": "The Anup Engineering Limited",
    "universalsompo.com": "Universal Sompo General Insurance",
    "vidalhealth.com": "Vidal Health TPA",
    "vipulmedcorp.com": "Vidal Health TPA",
    "sbi.co.in": "State Bank Of India",
    "volohealthtpa.com": "VOLO Health TPA",
}


def get_company_from_email(email_str):
    if not email_str:
        return "Unknown"
    email_str = email_str.strip().lower()
    if "@" not in email_str:
        return "Unknown"
    domain = email_str.split("@")[-1]
    if domain in DOMAIN_MAP:
        return DOMAIN_MAP[domain]
    for known_domain in DOMAIN_MAP:
        if domain.endswith("." + known_domain):
            return DOMAIN_MAP[known_domain]
    return "Unknown"


def extract_first_email(addr_field: str) -> str:
    if not addr_field:
        return ""
    match = re.search(r'[\w.\-+]+@[\w.\-]+', addr_field)
    return match.group(0) if match else ""


SHALBY_DOMAINS = ("shalby.in", "shalby.org", "griffin.co.in")


def _domain_of(addr_str: str) -> str:
    e = extract_first_email(addr_str)
    if not e or "@" not in e:
        return ""
    return e.split("@")[-1].strip().lower()


def _all_domains_of(addr_field: str) -> list:
    if not addr_field:
        return []
    parts = re.split(r",(?![^<]*>)", addr_field)
    domains = []
    for p in parts:
        d = _domain_of(p)
        if d:
            domains.append(d)
    return domains


def _is_shalby_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in SHALBY_DOMAINS)


def should_process_mail(from_addr: str, to_addr: str) -> bool:
    from_domain = _domain_of(from_addr)
    if not from_domain or not _is_shalby_domain(from_domain):
        return False
    to_domains = _all_domains_of(to_addr)
    if not to_domains:
        return False
    return any(not _is_shalby_domain(d) for d in to_domains)


def is_blacklisted(subject: str, from_addr: str, cfg: dict) -> bool:
    s = (subject or "").lower()
    f = (from_addr or "").lower()
    for kw in cfg.get("subjectBlacklist", []):
        if kw in s:
            return True
    for kw in cfg.get("senderBlacklist", []):
        if kw in f:
            return True
    return False


OUTSTANDING_KEYWORDS = ["outstanding", "pending", "follow up", "followup", "balance", "recovery", "due", "unpaid", "reminder"]
DEDUCTION_KEYWORDS = ["deduction", "short paid", "shortpaid", "shortfall", "disallow", "less payment", "recoup", "deducted"]


def classify_subject_intent(subject: str, body_snippet: str, client, model: str) -> str:
    s = subject.lower()
    has_out = any(k in s for k in OUTSTANDING_KEYWORDS)
    has_ded = any(k in s for k in DEDUCTION_KEYWORDS)

    if has_out and not has_ded:
        return "Outstanding"
    if has_ded and not has_out:
        return "Deduction"

    prompt = f"""Subject line: "{subject}"
Body snippet: "{body_snippet[:500]}"

Yeh email hospital insurance claim recovery se related hai. Decide karo ki
yeh email mainly kis baare me hai:
- "Outstanding" → abhi tak pending/unpaid amount jo insurer se lena baaki hai
- "Deduction" → insurer ne jo amount deduct/disallow/short-pay kiya hai
- "Other" → settlement confirmation, payment transfer, ya kuch aur unrelated

Sirf ek word reply karo: Outstanding, Deduction, ya Other.
"""
    try:
        resp = safe_generate_content(client, model, prompt, config=get_generation_config())
        ans = resp.text.strip().split()[0].strip(".,")
        if ans in ("Outstanding", "Deduction", "Other"):
            return ans
    except Exception:
        pass
    return "Other"


def pick_focus_amount(claims_df: pd.DataFrame, intent: str) -> float:
    if claims_df is None or claims_df.empty:
        return 0.0
    totals = calc_totals(claims_df)
    return totals.get(intent, 0.0) or 0.0


def _extract_amount_from_plain_body(body_text: str, intent: str) -> float:
    if not body_text:
        return 0.0
    text = body_text[:2500].lower()
    text = re.sub(r"\s+", " ", text)
    amount_re = r"rs\.?\s*[<\[\(]?\s*(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"

    if intent == "Outstanding":
        keywords = [
            "outstanding amount", "outstanding dues", "pending amount",
            "balance due", "net outstanding", "total outstanding",
            "outstanding of rs", "outstanding of rs.", "net outstanding as on",
        ]
    elif intent == "Deduction":
        keywords = [
            "deduction amount", "total deduction", "short paid amount",
            "deduction of rs", "deduction of rs.", "unjustified deduction", "diffrence",
        ]
    else:
        return 0.0

    for kw in keywords:
        pat = re.escape(kw) + r"\s*:?\s*" + amount_re
        m = re.search(pat, text)
        if m:
            amt = m.group(1).replace(",", "")
            try:
                return float(amt)
            except ValueError:
                pass

    for kw in keywords:
        pat = re.escape(kw) + r"(?:\s+\w+){0,5}\s*" + amount_re
        m = re.search(pat, text)
        if m:
            amt = m.group(1).replace(",", "")
            try:
                return float(amt)
            except ValueError:
                pass

    return 0.0


# #############################################################################
# SECTION 4 — GOOGLE SHEET (Service Account instead of Colab auth)
# #############################################################################

HEADER_ROW_D_ONWARDS = ["From", "To", "CC", "Subject", "Date", "Mail_Raw_Data", "Deduction", "Outstanding"]


def _json_safe(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return "" if np.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return value


def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable set nahi hai. "
            "Vercel project settings me service account ki puri JSON key (ek line me) "
            "is naam se env variable me daalein, aur us JSON ke 'client_email' ko "
            "apni Google Sheet me Editor access de dein."
        )
    info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def connect_google_sheet(sheet_url: str, worksheet_name: str):
    if not sheet_url:
        raise RuntimeError("Sheet URL nahi mila.")
    gc = get_gspread_client()
    sheet_id = sheet_url.split("/d/")[1].split("/")[0]
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet_name)
    return ws


def ensure_headers(ws):
    existing = ws.cell(1, 4).value
    if not existing:
        ws.update(values=[HEADER_ROW_D_ONWARDS], range_name="D1", value_input_option="USER_ENTERED")


def find_available_row(ws, unit, company):
    records = ws.get_all_values()
    for idx, row in enumerate(records[1:], start=2):
        if len(row) >= 2:
            if row[0].strip().lower() == unit.strip().lower() and row[1].strip().lower() == company.strip().lower():
                d_to_k = row[3:11] if len(row) >= 11 else row[3:] + [""] * (8 - len(row[3:]))
                if all(str(cell).strip() == "" for cell in d_to_k):
                    return idx
    return None


def append_unit_company_row(ws, unit, company):
    next_row = len(ws.get_all_values()) + 1
    ws.update(values=[[unit, company]], range_name=f"A{next_row}:B{next_row}", value_input_option="USER_ENTERED")
    return next_row


def write_mail_data_to_sheet(ws, row_idx, from_addr, to_addr, cc_addr, subject, date_str, raw_data, deduction_val, outstanding_val):
    raw_data_trimmed = (raw_data or "").strip()[:8000]
    values = [
        from_addr, to_addr, cc_addr, subject, date_str, raw_data_trimmed,
        deduction_val if deduction_val != "" else "",
        outstanding_val if outstanding_val != "" else "",
    ]
    values = [_json_safe(v) for v in values]
    ws.update(values=[values], range_name=f"D{row_idx}:K{row_idx}", value_input_option="USER_ENTERED")


# #############################################################################
# SECTION 5 — IMAP HELPERS — same as Colab
# #############################################################################

def save_email_to_disk(imap, msg_id, save_dir=TMP_DIR):
    os.makedirs(save_dir, exist_ok=True)
    status, msg_data = imap.fetch(msg_id, "(RFC822)")
    if status != "OK" or not msg_data:
        raise RuntimeError(f"IMAP fetch fail ho gaya mail {msg_id!r} ke liye (status={status})")

    raw_bytes = None
    for part in msg_data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            raw_bytes = part[1]
            break
    if raw_bytes is None:
        for part in msg_data:
            if isinstance(part, (bytes, bytearray)) and len(part) > 100:
                raw_bytes = part
                break
    if raw_bytes is None:
        raise RuntimeError(f"Mail {msg_id!r} ke liye raw email bytes nahi mile.")

    file_path = os.path.join(save_dir, f"mail_{msg_id.decode()}.eml")
    with open(file_path, "wb") as f:
        f.write(bytes(raw_bytes))
    return file_path


def get_to_cc(file_path):
    with open(file_path, "rb") as f:
        raw = f.read()
    msg = message_from_bytes(raw, policy=email.policy.default)
    return msg.get("To", "").strip(), msg.get("Cc", "").strip()


def get_message_id(file_path):
    with open(file_path, "rb") as f:
        raw = f.read()
    msg = message_from_bytes(raw, policy=email.policy.default)
    mid = msg.get("Message-ID", "") or msg.get("Message-Id", "")
    return mid.strip() if mid else file_path


def imap_login(cfg):
    imap = imaplib.IMAP4_SSL(cfg["imapHost"], int(cfg["imapPort"]))
    imap.login(cfg["imapUser"], cfg["imapPass"])
    return imap


def select_folder(imap, folder_name: str) -> bool:
    try:
        status, _ = imap.select(f'"{folder_name}"', readonly=True)
        if status == "OK":
            return True
    except Exception:
        pass
    alt_folder = folder_name.replace("/", ".")
    try:
        status, _ = imap.select(f'"{alt_folder}"', readonly=True)
        if status == "OK":
            return True
    except Exception:
        pass
    return False


def fetch_email_ids_in_folder(imap, from_date, to_date):
    criteria = f'(SINCE "{from_date}" BEFORE "{to_date}")'
    status, data = imap.search(None, criteria)
    if status == "OK" and data and data[0]:
        return data[0].split()
    return []


# #############################################################################
# SECTION 6 — MASTER PIPELINE (request-scoped, returns logs/stats to UI)
# #############################################################################

def run_full_pipeline(cfg: dict) -> dict:
    logs = []
    processed, skipped, errors = 0, 0, 0
    since_last_cooldown = 0
    processed_ids_this_run = set()

    def log(subject, from_addr, company, amount, status, date_str):
        logs.append({
            "subject": subject, "from": from_addr, "company": company,
            "amount": amount, "status": status, "date": date_str,
        })

    gemini_client = get_gemini_client(cfg["geminiApiKey"])
    gemini_model = cfg["geminiModel"]

    ws = connect_google_sheet(cfg["sheetUrl"], cfg["worksheetName"])
    ensure_headers(ws)

    imap = imap_login(cfg)

    for folder in cfg["folders"]:
        if not select_folder(imap, folder):
            continue

        msg_ids = fetch_email_ids_in_folder(imap, cfg["fromDate"], cfg["toDate"])

        for mid in msg_ids:
            try:
                file_path = save_email_to_disk(imap, mid)
            except Exception as e:
                errors += 1
                continue

            message_id = get_message_id(file_path)
            if message_id in processed_ids_this_run:
                continue

            try:
                meta = read_eml(file_path)
                to_field, cc_field = get_to_cc(file_path)

                if not should_process_mail(meta["from"], to_field):
                    skipped += 1
                    continue

                if is_blacklisted(meta["subject"], meta["from"], cfg):
                    skipped += 1
                    log(meta["subject"], meta["from"], "-", "-", "Blacklisted", meta["date_str"])
                    continue

                subject = meta["subject"]
                intent = classify_subject_intent(
                    subject, get_latest_message(meta.get("plain_text", ""))[:500], gemini_client, gemini_model
                )
                if intent == "Other":
                    skipped += 1
                    log(subject, meta["from"], "-", "-", "Skipped (Other)", meta["date_str"])
                    continue

                claims_df = extract_claims(meta, gemini_client, gemini_model, use_latest_only=True)
                focus_amount = pick_focus_amount(claims_df, intent)

                if intent == "Outstanding" and (focus_amount == 0 or focus_amount == 0.0):
                    if claims_df is not None and not claims_df.empty:
                        approved_total = claims_df["Approved"].apply(to_float).sum()
                        if approved_total > 0:
                            focus_amount = approved_total

                if not focus_amount:
                    latest_plain = get_latest_message(meta.get("plain_text", ""))
                    fb_amt = _extract_amount_from_plain_body(latest_plain, intent)
                    if fb_amt > 0:
                        focus_amount = fb_amt

                if not focus_amount and claims_df is not None and not claims_df.empty:
                    other_intent = "Deduction" if intent == "Outstanding" else "Outstanding"
                    other_amount = pick_focus_amount(claims_df, other_intent)
                    if other_amount > 0:
                        focus_amount = other_amount
                        intent = other_intent

                if not focus_amount:
                    focus_amount = "Follow Up Reminder"

                unit = get_unit_from_email(meta["from"])
                to_email = extract_first_email(to_field)
                company = get_company_from_email(to_email)

                row_idx = find_available_row(ws, unit, company)
                if row_idx is None:
                    row_idx = append_unit_company_row(ws, unit, company)

                date_str = meta["date"].strftime("%d/%m/%Y") if meta["date"] else meta["date_str"]

                deduction_val = focus_amount if intent == "Deduction" else ""
                outstanding_val = focus_amount if intent != "Deduction" else ""

                raw_data = meta.get("plain_text", "") or _html_to_text(meta.get("html_raw", ""))

                write_mail_data_to_sheet(
                    ws, row_idx, from_addr=meta["from"], to_addr=to_field, cc_addr=cc_field,
                    subject=subject, date_str=date_str, raw_data=raw_data,
                    deduction_val=deduction_val, outstanding_val=outstanding_val,
                )

                log(subject, meta["from"], company, focus_amount, f"{intent} → {unit}", date_str)
                processed += 1
                since_last_cooldown += 1
                processed_ids_this_run.add(message_id)

            except Exception as e:
                errors += 1
                log(meta.get("subject", "?") if "meta" in dir() else "?", "-", "-", "-", f"Error: {e}", "-")
                continue

            if since_last_cooldown >= BATCH_SIZE:
                time.sleep(BATCH_COOLDOWN_SECONDS)
                since_last_cooldown = 0

    imap.logout()

    return {"processed": processed, "skipped": skipped, "errors": errors, "logs": logs}


# #############################################################################
# SECTION 7 — FLASK ROUTES
# #############################################################################

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/defaults")
def api_defaults():
    """Frontend ko non-secret defaults bhejta hai (password/key kabhi wapas nahi bhejenge)."""
    safe = dict(DEFAULTS)
    safe.pop("imapPass", None)
    safe.pop("geminiApiKey", None)
    safe["hasGeminiKey"] = bool(DEFAULTS["geminiApiKey"])
    safe["hasImapPass"] = bool(DEFAULTS["imapPass"])
    return jsonify(safe)


@app.route("/api/run", methods=["POST"])
def api_run():
    body = request.get_json(force=True, silent=True) or {}
    cfg = cfg_from_request(body)
    try:
        result = run_full_pipeline(cfg)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sheet-data", methods=["POST"])
def api_sheet_data():
    body = request.get_json(force=True, silent=True) or {}
    cfg = cfg_from_request(body)
    try:
        ws = connect_google_sheet(cfg["sheetUrl"], cfg["worksheetName"])
        values = ws.get_all_values()
        if not values:
            return jsonify({"ok": True, "headers": [], "rows": []})
        headers = values[0]
        rows = values[1:]
        return jsonify({"ok": True, "headers": headers, "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ai-chat", methods=["POST"])
def api_ai_chat():
    body = request.get_json(force=True, silent=True) or {}
    api_key = body.get("apiKey") or DEFAULTS["geminiApiKey"]
    model = body.get("model") or DEFAULTS["geminiModel"]
    prompt = body.get("prompt", "")
    if not prompt:
        return jsonify({"ok": False, "error": "prompt khaali hai"}), 400
    try:
        client = get_gemini_client(api_key)
        resp = safe_generate_content(client, model, prompt, config=get_generation_config())
        return jsonify({"ok": True, "reply": (resp.text or "").strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
