import os
import json
import base64
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
SHEET_PREFIX = os.environ.get("SHEET_PREFIX", "Nalanda")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

DOC_TYPES = {
    "TAX_INVOICE": "Tax Invoice",
    "PURCHASE_INVOICE": "Purchase Invoice",
    "SALES_INVOICE": "Sales Invoice",
    "DELIVERY_CHALLAN": "Delivery Challan",
    "WEIGH_SLIP": "Weigh Slip",
    "RECEIPT": "Receipt",
    "PURCHASE_ORDER": "Purchase Order",
    "CREDIT_NOTE": "Credit Note",
    "DEBIT_NOTE": "Debit Note",
    "OTHER": "Other"
}

PROMPT = """You are a business document scanner for Nalanda Concrete Blocks, Bengaluru, India.
Analyse this document image and respond ONLY with valid JSON, no markdown, no extra text.

Identify document type from: TAX_INVOICE, DELIVERY_CHALLAN, WEIGH_SLIP, PURCHASE_INVOICE,
SALES_INVOICE, RECEIPT, PURCHASE_ORDER, CREDIT_NOTE, DEBIT_NOTE, OTHER.

Extract all visible fields like invoice_number, invoice_date, supplier_name, supplier_gstin,
buyer_name, vehicle_number, gross_weight_kg, tare_weight_kg, net_weight_kg, material,
quantity_to, unit_price, basic_amount, cgst, sgst, total_amount, rst_number.

Return ONLY this JSON structure:
{"doc_type":"TAX_INVOICE","confidence":"HIGH","fields":{"key":"value"}}"""


def send_message(chat_id, text, parse_mode="Markdown"):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    })


def get_file_url(file_id):
    res = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
    data = res.json()
    if not data.get("ok"):
        return None
    file_path = data["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"


def download_image(url):
    res = requests.get(url)
    return base64.b64encode(res.content).decode("utf-8")


def analyse_with_claude(image_b64):
    res = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01"
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": PROMPT}
                ]
            }]
        }
    )
    data = res.json()
    if "error" in data:
        raise Exception(data["error"]["message"])
    raw = data["content"][0]["text"].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end+1]
    return json.loads(raw)


def save_to_sheets(extracted, from_user):
    if not APPS_SCRIPT_URL:
        return False
    label = DOC_TYPES.get(extracted["doc_type"], extracted["doc_type"])
    sheet_name = f"{SHEET_PREFIX} - {label}"
    fields = extracted.get("fields", {})
    headers = ["Received From", "Doc Type", "Confidence"] + [
        k.replace("_", " ").title() for k in fields.keys()
    ]
    row = [from_user, label, extracted.get("confidence", "")] + list(fields.values())
    try:
        requests.post(APPS_SCRIPT_URL, json={
            "sheetName": sheet_name,
            "headers": headers,
            "row": row
        }, timeout=15)
        return True
    except Exception:
        return False


def format_reply(extracted, saved):
    label = DOC_TYPES.get(extracted["doc_type"], extracted["doc_type"])
    confidence = extracted.get("confidence", "")
    fields = extracted.get("fields", {})

    # Build summary line
    total = fields.get("total_amount", fields.get("net_weight_kg", ""))
    supplier = fields.get("supplier_name", fields.get("company", ""))
    inv_num = fields.get("invoice_number", fields.get("rst_number", ""))

    lines = [f"*{label}* — {confidence} confidence"]
    if inv_num:
        lines.append(f"No: `{inv_num}`")
    if supplier:
        lines.append(f"From: {supplier}")
    if total:
        lines.append(f"Amount/Wt: *{total}*")

    lines.append("")
    lines.append("*Extracted fields:*")
    for k, v in list(fields.items())[:8]:
        label_k = k.replace("_", " ").title()
        lines.append(f"• {label_k}: `{v}`")

    if len(fields) > 8:
        lines.append(f"_...and {len(fields)-8} more fields_")

    lines.append("")
    if saved:
        lines.append("✅ Saved to Google Sheets")
    else:
        lines.append("⚠️ Could not save to Sheets")

    return "\n".join(lines)


def process_image(chat_id, file_id, from_name):
    send_message(chat_id, "⏳ Reading document...")
    try:
        file_url = get_file_url(file_id)
        if not file_url:
            send_message(chat_id, "❌ Could not download image.")
            return
        image_b64 = download_image(file_url)
        extracted = analyse_with_claude(image_b64)
        saved = save_to_sheets(extracted, from_name)
        reply = format_reply(extracted, saved)
        send_message(chat_id, reply)
    except Exception as e:
        send_message(chat_id, f"❌ Error: {str(e)}")


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return "ok"

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    from_user = message.get("from", {})
    from_name = from_user.get("first_name", "") + " " + from_user.get("last_name", "")
    from_name = from_name.strip() or from_user.get("username", "Unknown")

    # Handle photo
    if "photo" in message:
        # Get largest photo
        file_id = message["photo"][-1]["file_id"]
        process_image(chat_id, file_id, from_name)

    # Handle document (PDF or image sent as file)
    elif "document" in message:
        doc = message["document"]
        mime = doc.get("mime_type", "")
        if mime.startswith("image/"):
            process_image(chat_id, doc["file_id"], from_name)
        else:
            send_message(chat_id, "⚠️ Please send images only (JPG/PNG). PDF support coming soon.")

    # Handle text commands
    elif "text" in message:
        text = message["text"].strip()
        if text == "/start":
            send_message(chat_id,
                "*Nalanda Doc Scanner Bot* 📄\n\n"
                "Send me any business document photo and I will:\n"
                "• Identify the document type\n"
                "• Extract all fields\n"
                "• Save to Google Sheets automatically\n\n"
                "Supports: Tax Invoice, Delivery Challan, Weigh Slip, Purchase Invoice, Receipt, PO and more.\n\n"
                "Just send a photo to get started! 📷"
            )
        elif text == "/help":
            send_message(chat_id,
                "*Commands:*\n"
                "/start — Welcome message\n"
                "/help — Show this help\n"
                "/status — Check bot status\n\n"
                "Just send any document photo to scan it."
            )
        elif text == "/status":
            claude_ok = bool(CLAUDE_API_KEY)
            sheets_ok = bool(APPS_SCRIPT_URL)
            send_message(chat_id,
                f"*Bot Status*\n"
                f"Claude API: {'✅' if claude_ok else '❌'}\n"
                f"Google Sheets: {'✅' if sheets_ok else '❌'}\n"
                f"Sheet prefix: {SHEET_PREFIX}"
            )

    return "ok"


@app.route("/", methods=["GET"])
def home():
    return "Nalanda Bot is running", 200


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    webhook_url = request.args.get("url", "")
    if not webhook_url:
        return "Pass ?url=https://your-app.railway.app/webhook", 400
    res = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": webhook_url})
    return res.json()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
