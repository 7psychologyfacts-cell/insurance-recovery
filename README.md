# IP & OP Claim Processing & Aging Analytics Engine 🏥📊

A high-performance Python-based data engineering and web analytical platform engineered to streamline healthcare claim processing, claim dispatch tracking, unit-wise aging metrics, and dynamic report generation.

---

## 📌 Project Overview

In healthcare operations, managing Inpatient (IP) and Outpatient (OP) claim workflows requires clear tracking of Turnaround Times (TAT) to prevent delays and bottlenecks. 

This repository contains an end-to-end web engine built with **Flask** and **Pandas** that ingests raw claims data, executes vectorized aging calculations, segregates Cyclic and Non-Cyclic files into defined operational bins, and outputs executive-ready formatted Excel reports instantly.

---

## 🔥 Key Technical Highlights

- **Unified Claims Consolidation**: Seamlessly reads and merges multi-sheet IP & OP datasets into a normalized processing pipeline.
- **Precision Aging & TAT Calculation**:
  - Dynamically calculates aging metrics based on invoice dates, file types, and predefined operational thresholds.
  - **Non-Cyclic Processing**: Binned into standard SLAs (`0-7`, `8-30`, `31-60`, `61-90`, `91-180`, `181-365`, `>365` days).
  - **Cyclic Processing**: Customized interval binning (`0-31`, `31-60`, `61-90`, `91-180`, `181-365`, `>365` days).
- **Automated Summary Matrix**: Constructs pivot aggregates grouped by operational units, highlighting pending counts and total distribution across bins.
- **In-Memory Excel Processing (`io.BytesIO`)**: Bypasses local file storage I/O bottlenecks to deliver high-speed, direct stream downloads.
- **Custom Excel Styling (`XlsxWriter`)**:
  - Multi-tab breakdown: Clean detailed data view (`Data`) + High-level KPI summary (`Summary`).
  - Professional styling with custom header fills, number formats, date formatting, freeze panes, and alternating band colors.
- **Minimalist Web UI**: Interactive file uploader equipped with client-side drag-and-drop validation, loading states, and exception handling.

---

## 🛠️ Technology Stack

| Layer | Technology / Library |
| :--- | :--- |
| **Language** | Python 3.x |
| **Web Framework** | Flask 3.0 |
| **Data Processing** | Pandas 2.2, NumPy 1.26 |
| **Excel Engineering** | XlsxWriter 3.2, OpenPyXL 3.1 |
| **Frontend** | HTML5, CSS3, JavaScript (Fetch API) |
| **Deployment** | Serverless ready (Vercel / Gunicorn) |

---

## 📁 Repository Structure

```text
├── app.py                             # Main Flask application & core ETL pipeline
├── claim-not-dispatch-Sample Data.xls # Sample operational dataset for testing
├── requirements.txt                   # Dependency specifications
└── vercel.json                        # Serverless deployment configuration
```

---

## ⚡ Quick Start & Execution

### 1. Repository Setup
```bash
git clone https://github.com/your-username/ip-op-claims-engine.git
cd ip-op-claims-engine
```

### 2. Environment Configuration
It is recommended to use a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Launch Application
```bash
python app.py
```
Access the application by opening `http://localhost:5000` in your web browser.

---

## 📈 Operational Impact

- **Automation Speed**: Replaces hours of manual Excel lookup formulas with sub-second execution.
- **Data Integrity**: Vectorized filtering eliminates broken row references and manual pivot errors.
- **Operational Clarity**: Gives healthcare management an immediate line of sight into backlogged claims across all facilities.

---

## 🤝 Contributing & Support

Contributions, feature requests, and optimizations are welcome! Feel free to open an issue or submit a pull request.
