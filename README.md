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
```

### Key UI Actions

* **Preview Payload:** Generates and displays the final JSON message structure for the first recipient **without sending**.
* **Generate CSV:** Saves a CSV file compatible with `send_batch.py`.
* **Media Menu:** Facilitates uploading local files and reusing cached media IDs.
    * **Upload Media:** Uploads a local file to the Meta `/media` endpoint, copies the resulting `media_id` to the clipboard, and inserts it into the current header settings.
    * **Media Library:** Browse previously uploaded IDs stored in `media_cache.json`.

---

## 📝 Behavior Notes & Advanced Usage

### CSV Columns Overview

The tool uses a flexible CSV schema. Columns marked **Shared** are used by both template and interactive modes.

| Column | Shared/Template/Interactive | Description | Example |
| :--- | :--- | :--- | :--- |
| `phone` | Shared | E.164 phone number. | `15551234567` |
| `msg_type` | Shared | Defines message type (`template` or `interactive`). | `template` |
| `template` | Template | Approved template name. | `order_update` |
| `lang` | Template | Language code (default `en_US`). | `es` |
| `body_params` | Template | Pipe-separated list for body variables. | `John|#1234|tracking-link` |
| `header_type` | Template | `none`, `text`, `image`, `video`, or `document`. | `image` |
| `header_media_path` | Template | Local path to media file (uploaded and cached). | `assets/logo.png` |
| `button_params` | Template | Comma-separated dynamic button params (URL suffix). | `A1|B2,C3` |
| `cta{n}_coupon_code` | Template (Copy Code) | The actual coupon code for the button. | `SUMMER24` |
| `body_text` | Interactive | Free-form message body. | `Your order is ready.` |
| `cta0_type` | Interactive | CTA type (`url` or `call`). | `url` |

### Media Caching

The tool saves uploaded media IDs in `media_cache.json`. The key is a **content hash** of the local file. If a file's content hasn't changed, the tool **reuses the cached `media_id`**, preventing unnecessary re-uploads and saving time.

### Sending Interactive Messages

To send an interactive message, ensure the `msg_type` column is set to **`interactive`**. The required fields are `phone`, `body_text`, and the first CTA fields: `cta0_type` (`url` or `call`), `cta0_text`, and either `cta0_url` or `cta0_phone`.

### Sending Coupon Template Messages

The tool abstracts the complexity of Meta's Coupon/Copy Code template buttons. When `send_batch.py` encounters a CTA defined with `type=copy_code` and a `cta{n}_coupon_code`, it automatically constructs the required template component with `sub_type=COPY_CODE` and the coupon parameter.

---

## 🔒 Security

**Always keep your `.env` file and its sensitive tokens out of your source control.** The provided `.gitignore` file already ignores it by default.
