# ✨ WhatsApp Template Batch Sender (Python CLI & UI)

Send **WhatsApp Business API (WABA) template messages** in bulk and **Interactive Messages** directly from a CSV file. This tool provides both a powerful **Command Line Interface (CLI)** for automated sends and a **Desktop UI** for easy CSV creation and payload previewing.

## 🚀 Overview

This Python utility is designed for developers and marketers needing to send high-volume, structured WhatsApp messages via the **Cloud API**.

* **Core Function:** Parses a CSV to build and dispatch WABA template messages or Interactive/CTA messages.
* **Media Handling:** Supports both **local media file upload** (with automatic caching) or remote **media URLs**.
* **Flexibility:** Handles header media/text, body parameters, and dynamic URL button parameters.
* **Safety:** Includes a **Dry-Run mode** to inspect JSON payloads before sending.
* **Productivity:** Features a **Desktop UI** to visually construct and validate payloads, manage media, and generate the required CSV format.

---

## 🌟 Features

* **Batch Sending:** Process hundreds or thousands of recipients from a single CSV.
* **Intelligent Media Caching:** Automatically uploads local media files to the Graph API and **caches the returned media ID** locally (`media_cache.json`) based on the file's content hash. This prevents unnecessary re-uploads.
* **Full Template Support:**
    * Header: `text`, `image`, `video`, or `document`.
    * Body: Positional parameter mapping.
    * Buttons: Dynamic suffix support for `URL` buttons and built-in handling for Meta's `Copy Code` (Coupon) button type.
* **Interactive Message Support:** Send messages with custom body text, footer, and a single `URL` or `Call-To-Action (CTA)` button.
* **Desktop App:** A Tkinter based GUI to easily:
    * Build recipient CSVs for both `Template` and `Interactive` messages.
    * Preview the final JSON message payload for validation.
    * Upload and manage your Meta media assets.
* **Error Logging:** All API responses and errors are logged to `logs/sent_*.jsonl`.

---

## 🛠️ Prerequisites

* **WhatsApp Business Cloud API Access (WABA):** You need an active **Phone Number ID** connected to your Meta app.
* **Access Token:** A **permanent access token** with the necessary permissions (`whatsapp_business_management`, `whatsapp_business_messaging`).
* **Python:** **Python 3.9+** is recommended.

---

## ⚡ Quick Start (CLI)

1.  **Environment Setup:** Create a `.env` file based on `.env.example` and set your credentials:

    ```ini
    WHATSAPP_TOKEN="YOUR_PERMANENT_ACCESS_TOKEN"
    WHATSAPP_PHONE_NUMBER_ID="YOUR_WABA_PHONE_NUMBER_ID"
    # Optional, defaults to v20.0
    WHATSAPP_API_VERSION="v20.0" 
    ```

2.  **Prepare CSV:** Prepare your recipient list. See `samples/recipients.csv` for column structure.

3.  **Install & Dry Run:** Install dependencies and run a test to preview the payloads.

    ```bash
    pip install -r requirements.txt
    python send_batch.py --input samples/recipients.csv --dry-run
    ```

4.  **Send Messages:** Once the dry-run output looks correct, remove the `--dry-run` flag:

    ```bash
    python send_batch.py --input samples/recipients.csv
    ```

---

## ⚙️ CLI Options

| Option | Description | Default |
| :--- | :--- | :--- |
| `--input <path>` | **Required.** Path to the recipient CSV file. | N/A |
| `--dry-run` | Build and log the JSON payloads without sending. **Recommended for testing.** | `False` |
| `--delay-ms <ms>` | Milliseconds delay between each message send. | `0` |
| `--max <num>` | Limit the number of rows processed. Useful for small tests. | All |
| `--token` | Override the `WHATSAPP_TOKEN` value from `.env`. | `.env` value |
| `--phone-number-id` | Override the `WHATSAPP_PHONE_NUMBER_ID` value from `.env`. | `.env` value |
| `--api-version` | Override the API version. | `v20.0` |

---

## 💻 Desktop UI (CSV Builder)

The UI streamlines the process of creating the complex CSV formats and managing media assets.

**Launch:**

```bash
python ui_app.py
