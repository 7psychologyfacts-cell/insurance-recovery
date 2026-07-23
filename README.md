# 🛡️ Insurance Recovery & Credit Collection MIS Automation System

A production-ready, full-stack enterprise automation system built to streamline **hospital insurance claim recovery, deduction analytics, and credit collection MIS reporting**. 

Designed specifically for multi-unit healthcare organizations handling large volumes of insurer and TPA (Third Party Administrator) communications, this system automates end-to-end workflows—from fetching IMAP emails and parsing complex claim tables using AI to populating central Google Sheets and rendering an interactive, Excel-like analytical dashboard.

---

## ⚙️ Operational Impact

* **90%+ Reduction in Manual Processing Effort:** Eliminates thousands of hours spent by revenue cycle management (RCM) and billing teams manually copying claim figures, settlement notes, and deduction details from emails into spreadsheets.
* **Elimination of Human Error in Settlement Tracking:** Deterministic HTML parsing combined with AI validation guarantees structured numerical extraction without typos or missed line items.
* **Real-time Dispute & Deduction Visibility:** Instantly flags settlement shortfalls, partial approvals, and unacknowledged claims across units, enabling billing teams to appeal claim deductions within statutory windows.
* **Audit-Ready Operational Tracking:** Built-in dual logging ensures every claim communication, status update, and processing step is logged in `Sheet2` with raw UIDs for historical audit compliance.

---

## 💼 Business Impact

* **Accelerated Cash Flow & Reduced Days Sales Outstanding (DSO):** Faster extraction and centralized tracking directly speed up claim reconciliation, secondary billing, and final collection follow-ups.
* **Scale Without Incremental Headcount:** Enables multi-hospital networks to scale patient volume and insurance claims handling across 10+ units without needing linear expansion of back-office billing personnel.
* **Cost-Optimized AI Pipeline:** Implements a intelligent 3-tier parsing hierarchy (HTML -> Regex -> Gemini AI) that keeps LLM API token consumption low by routing simple tabular data through zero-cost local parsers.
* **Data-Driven Payer Negotiation:** Executive dashboards and heatmaps provide leadership with clear, quantitative leverage when negotiating tariff contracts and settlement terms with government and private TPAs (CGHS, ECHS, RGHS, ISRO, ONGC, etc.).

---

## 👨‍💻 Developer Information

* **Architecture:** Modular, serverless-ready Python WSGI Flask engine paired with an asynchronous single-page web dashboard (Vanilla JS ES6+).
* **AI & Machine Learning:** Integrated with Google GenAI SDK (`gemini-3.1-flash-lite` / `gemini-1.5-pro`) using structured prompt engineering and fallback error handling.
* **Database & Storage:** Google Sheets API v4 (`gspread`) as a real-time serverless database layer with automated sheet initialization, column normalization, and dual-tab logging.
* **Deployment & Ops:** Pre-configured for zero-downtime serverless deployments on Vercel (`vercel.json`), supporting stateless execution via IMAP UID-based state management.

---

## 🌟 Key Capabilities & Features

### 📬 1. Smart Email Ingestion & Filtering (IMAP)
* **Multi-Folder IMAP Sync:** Programmatically connects to enterprise mailbox folders across different hospital units (e.g., Jaipur, Indore, SG, Mohali, Surat, Vapi, Naroda, Jabalpur, Krishna).
* **UID-Based Processing Engine:** Fetches email messages by UID to ensure stateless, idempotent processing across distributed serverless environments without sequence number conflicts.
* **Intelligent Routing & Domain Matching:** Filters internal hospital emails vs. external insurer/TPA emails automatically using dynamic regex domain parsing and configurable blacklists (subject/sender filters).

### 🧠 2. Dynamic Multi-Tier Claim Extraction Pipeline
To maximize accuracy and throughput while keeping LLM costs minimal, the system processes every email through a deterministic 3-tier parsing hierarchy:
1. **Tier 1 — HTML Table Engine:** Scrapes and normalizes raw HTML tables using semantic header mapping (`HEADER_SYNONYMS`) for instant structural extraction.
2. **Tier 2 — Plain Text Regex Engine:** Cleans line breaks and tokenizes plain text bodies to match structured tabular records via pattern recognition.
3. **Tier 3 — Google Gemini LLM Integration:** Fallback to Google Gemini AI models (`gemini-3.1-flash-lite`, `gemini-1.5-pro`) with custom structured prompt instructions to parse unstructured body copy and edge-case table layouts with robust numerical validation.

### 📊 3. Live Google Sheets Integration & Audit Trail
* **Automated Data Mapping:** Dynamically resolves hospital unit codes and corporate insurer domains, appending extracted claim figures directly into dedicated Google Sheets (`Sheet1`).
* **Complete Audit Trail (`Sheet2`):** Every processed, skipped, or failed email attempt is logged with timestamp, sender, domain, intent classification, and raw UID, enabling full auditability and historical replay.

### 🖥️ 4. Enterprise MIS Dashboard & Interactive UI
* **Live Email Processing Monitor:** Real-time progress tracking bar, step-by-step logs, and instant email previewing (HTML rendering + attachment downloading) without leaving the dashboard.
* **Dynamic Pivot Table Builder:** Built-in Excel-style drag-and-drop pivot builder supporting custom rows, columns, values (Sum, Count, Average, Min, Max), and live field filtering.
* **Executive Overview & KPI Analytics:** Chart.js visual analytics for Unit-wise Outstanding vs. Mail Outstanding, status breakdowns, and heatmaps for major government payers (CGHS, ECHS, RGHS, ISRO, ONGC, CISF).
* **🤖 AI-Robo Contextual Assistant:** Embedded grounded AI chatbot that reads current Google Sheet data and answers natural language query requests strictly from real-time operational context.
* **Full Data Exportability:** Native client-side exporting to styled Excel (.xlsx) workbooks powered by `ExcelJS` and `XLSX`.

---

## 🏗️ Architecture & Technical Stack

```
   ┌────────────────┐      IMAP (SSL)     ┌───────────────────────┐
   │ Enterprise     ├────────────────────►│  Flask Web Server     │
   │ Email Folders  │                     │  (Vercel Serverless)  │
   └────────────────┘                     └──────────┬────────────┘
                                                     │
                                   ┌─────────────────┼─────────────────┐
                                   ▼                 ▼                 ▼
                          ┌────────────────┐ ┌───────────────┐ ┌───────────────┐
                          │ Deterministic  │ │  Google GenAI │ │ Google Sheets │
                          │ HTML/Regex Engine│ │ (Gemini API) │ │ (gspread/SA)  │
                          └────────────────┘ └───────────────┘ └───────┬───────┘
                                                                       │
                                                                       ▼
                                                          ┌─────────────────────────┐
                                                          │ Interactive UI          │
                                                          │ (Pivot, Charts, AI-Robo)│
                                                          └─────────────────────────┘
```

* **Backend Engine:** Python 3, Flask, `gspread` (Google Sheets API v4), `google-genai` SDK, `pandas`, `numpy`, `imaplib`, `email`.
* **Frontend Dashboard:** Vanilla JavaScript (ES6+), HTML5, Modern CSS3 Flexbox/Grid, Chart.js 4.4, ExcelJS, SheetJS (XLSX).
* **Deployment Setup:** Optimized for Vercel Serverless Functions via `@vercel/python` WSGI gateway.

---

## 📁 Repository Structure

```
.
├── app.py              # Main Flask backend application (IMAP, Gemini AI, Sheets Sync, APIs)
├── index.html          # Single Page Application (SPA) dashboard, Pivot engine, UI components
├── vercel.json         # Vercel deployment routes and builder specifications
├── requirements.txt    # Python dependencies
└── README.md           # Documentation
```

---

## 🚀 Setup & Installation

### 1. Prerequisites
* Python 3.10 or higher
* A Google Cloud Project with Google Sheets API & Google Drive API enabled
* A Google Gemini API Key
* An IMAP-enabled email account with App Password support

### 2. Local Environment Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/your-username/insurance-recovery-mis.git
cd insurance-recovery-mis

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables Configuration

Create a `.env` file or export the following variables in your terminal:

```env
# Gemini API Setup
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.1-flash-lite

# Google Sheets Setup
SHEET_URL=https://docs.google.com/spreadsheets/d/your_sheet_id/edit
WORKSHEET_NAME=Sheet1
GOOGLE_SERVICE_ACCOUNT_JSON={"type": "service_account", "project_id": "...", ...}

# IMAP Setup
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your_email@organization.com
IMAP_PASS=your_app_password
```

> **Note:** Ensure you grant `Editor` access on your Google Sheet to the `client_email` listed inside your Google Service Account JSON.

### 4. Running the Application Locally

```bash
python app.py
```
Open your browser and navigate to `http://localhost:5000` to access the dashboard.

---

## ☁️ Serverless Deployment (Vercel)

This repository is pre-configured for seamless Vercel deployment using `@vercel/python`.

1. Install Vercel CLI: `npm i -g vercel`
2. Run `vercel` in the project root directory.
3. Configure the environment variables (`GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `IMAP_PASS`, etc.) directly in the **Vercel Project Settings → Environment Variables** dashboard.

---

## 🔑 Key API Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `GET /` | `GET` | Serves the main MIS Dashboard user interface (`index.html`). |
| `GET /api/defaults` | `GET` | Retrieves non-sensitive environment defaults for UI pre-filling. |
| `POST /api/list-emails` | `POST` | Discovers target emails across configured IMAP folders by date range. |
| `POST /api/process-email` | `POST` | Processes a single email by folder & UID, executes extraction, and syncs to Sheets. |
| `POST /api/log-history` | `POST` | Reads processing history and status logs directly from `Sheet2`. |
| `POST /api/email-preview` | `POST` | Fetches raw HTML/text preview and base64 attachments for deep inspection. |
| `POST /api/sheet-data` | `POST` | Pulls real-time dataset from Google Sheets for local UI preview & pivot reporting. |
| `POST /api/ai-chat` | `POST` | Grounded AI assistant endpoint powered by Gemini over Sheet context. |

---

## 🛡️ License & Contributing

This project is open-source and available under the **MIT License**. Contributions, feature suggestions, and pull requests are welcome.
