WhatsApp Template Batch Sender (Python CLI)

Overview
- Send WhatsApp Business (WABA) template messages in batches from a CSV.
- Handles media via either upload to the Graph `/media` endpoint or by link.
- Caches uploaded media IDs locally to avoid re-uploading the same file.
- Supports header media/text, body parameters, and URL button parameters.
- Dry-run mode to preview payloads before sending.

Prerequisites
- WhatsApp Business Cloud API access (WABA) with an active phone number ID.
- A permanent access token with permissions to send messages.
- Python 3.9+ recommended.

Quick Start
1) Create a `.env` file based on `.env.example` and set:
   - `WHATSAPP_TOKEN`
   - `WHATSAPP_PHONE_NUMBER_ID`
   - Optionally `WHATSAPP_API_VERSION` (defaults to `v20.0`)

2) Prepare a CSV like `samples/recipients.csv` with columns:
   - `phone`: E.164 number (e.g., 15551234567)
   - `template`: Template name (e.g., `order_update`)
   - `lang`: Language code (default `en_US` if omitted)
   - `body_params`: Pipe-separated parameters for the template body, e.g. `John|#1234`
   - `header_type`: one of `none|text|image|video|document` (optional)
   - `header_text`: text content when `header_type=text` (optional)
   - `header_media_path`: local path to media (when type is image/video/document). Will be uploaded and cached.
   - `header_media_url`: remote URL to media (alternative to media_path; won’t be uploaded)
   - `button_params`: comma-separated for each button, each entry pipe-separated if multiple params
     - Example for two URL buttons with 1 dynamic parameter each: `A1,B2`

3) Install deps and run a dry run:
   - `pip install -r requirements.txt`
   - `python send_batch.py --input samples/recipients.csv --dry-run`

4) Send for real (remove `--dry-run`):
   - `python send_batch.py --input samples/recipients.csv`

CLI Options
- `--input`: Path to the CSV file.
- `--token`, `--phone-number-id`, `--api-version`: Override `.env` values.
- `--delay-ms`: Milliseconds delay between sends (default 0).
- `--max`: Limit number of rows to process (for testing).
- `--dry-run`: Build and log payloads without sending.

Behavior Notes
- Media caching: A local `media_cache.json` stores uploaded media IDs keyed by file content hash. If a file’s content hasn’t changed, the cached `id` will be reused.
- Template components:
  - Header: supports `text`, `image`, `video`, `document`, or none.
  - Body: parameters mapped positionally from `body_params` to the template variables.
  - Buttons: URL button dynamic suffix parameters supported via `button_params`. Quick reply buttons are static and do not require parameters.
- Errors and responses: Written to `logs/sent_*.jsonl`.

Limitations
- This tool focuses on the most common patterns (text/body variables, header media or text, URL button params). If you need currency/date types or other advanced constructs, open an issue or extend `whatsapp_client.py` accordingly.

Security
- Keep your `.env` out of source control. The `.gitignore` file ignores it by default.

Desktop UI (CSV Builder)
- Launch: `python ui_app.py`
- Purpose: Create a recipients CSV for either template messages or interactive CTA messages.
- Actions:
  - Preview Payload: Shows the JSON payload for the first phone without sending.
  - Generate CSV: Saves a CSV compatible with `send_batch.py`.
- Notes:
  - Message Type:
    - `Template`: use an existing approved template (name + lang). You can add body params, URL button params (dynamic suffix), optional Flow buttons.
    - `Interactive (CTA)`: compose a free-form body and footer text, plus one CTA (`url` or `call`). `copy_code` CTAs are template-only because the interactive API does not support coupon-code buttons.
 - Media header source: choose exactly one — Local File (uploaded and cached by `send_batch.py`), URL (no upload), or existing `media_id`.
  - URL button params: comma-separated button groups; each group is `|`-separated (e.g., `A1|B2,C3`).
  - Flow buttons: optional; add one or more with index, token, action, and navigate screen.
  - Templates menu: Load and select from `templates/archive.json` to auto-fill header type, example media URL, language, and required Flow button entries.
  - Media menu:
    - Upload Media: Pick a local file and upload it to Meta. The returned media ID is copied to clipboard and can be set into the header as `media_id`.
    - Media Library: Browse previously uploaded items from `media_cache.json`, copy IDs, and insert directly into the header.
  - Header media: choose exactly one source. If you use Upload, the UI will switch the source to `Media ID` automatically.

CSV Columns
- Shared: `phone`, `msg_type`
- Template mode: `template`, `lang`, `body_params`, `header_type`, `header_text`, `header_media_path`, `header_media_url`, `header_media_id`, `button_params`, repeated `button{n}_*` for Flow.
- Interactive mode: `body_text`, `footer_text`, and `cta{n}_type|text|url|phone` (only the first CTA is used for interactive preview/sending logic and must be `url` or `call`).
- CTA columns always include `cta{n}_coupon_code`. Provide this when defining coupon buttons (`type=copy_code`) for template sending; coupon buttons are indexed automatically in the order you add them. See [Meta's coupon template documentation](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/coupon-templates).

Sending Interactive Messages
- Create a CSV with `msg_type=interactive` and at least: `phone`, `body_text`, and the first CTA fields: `cta0_type` (`url` or `call`) plus `cta0_text` and `cta0_url` or `cta0_phone` accordingly. Optional: `footer_text`.
- Run: `python send_batch.py --input <your_csv>` (supports `--dry-run` to preview). Template-specific columns are ignored when `msg_type=interactive`.

Sending Coupon Template Messages
- Add a CTA row with `type=copy_code` and set `cta{n}_coupon_code`. The order you add coupon CTAs becomes the WhatsApp button index automatically.
- When `send_batch.py` sees a `copy_code` CTA it automatically emits a template component with `sub_type=COPY_CODE` and the required `coupon_code` parameter so you can deliver Meta's coupon templates without manual JSON edits.
