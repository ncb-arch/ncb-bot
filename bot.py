import os
import sys
import json
import base64
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
SHEET_PREFIX = os.environ.get("SHEET_PREFIX", "Nalanda")

TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_TOKEN

print("Bot starting...", flush=True)
print("TOKEN set:", bool(TELEGRAM_TOKEN), flush=True)
print("CLAUDE set:", bool(CLAUDE_API_KEY), flush=True)
print("SHEETS set:", bool(APPS_SCRIPT_URL), flush=True)

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
Return ONLY this JSON: {"doc_type":"TAX_INVOICE","confidence":"HIGH","fields":{"key":"value"}}"""


def send_message(chat_id, text, parse_mode="Markdown"):
    print("Sending message to:", chat_id, flush=True)
    try:
        r = requests.post(TELEGRAM_API + "/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }, timeout=10)
        print("Send result:", r.status_code, flush=True)
    except Exception as e:
        print("Send error:", str(e), flush=True)


def get_file_url(file_id):
    res = requests.get(TELEGRAM_API + "/getFile", params={"file_id": file_id}, timeout=10)
    data = res.json()
    if not data.get("ok"):
        return None
    file_path = data["result"]["file_path"]
    return "https://api.telegram.org/file/bot" + TELEGRAM_TOKEN + "/" + file_path


def download_image(url):
    res = requests.get(url, timeout=30)
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
            "model": "claude-sonnet-4-5",
            "max_tokens": 1000,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": PROMPT}
                ]
            }]
        },
        timeout=30
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
    sheet_name = SHEET_PREFIX + " - " + label
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
    except Exception as e:
        print("Sheets error:", str(e), flush=True)
        return False


def format_reply(extracted, saved):
    label = DOC_TYPES.get(extracted["doc_type"], extracted["doc_type"])
    confidence = extracted.get("confidence", "")
    fields = extracted.get("fields", {})
    total = fields.get("total_amount", fields.get("net_weight_kg", ""))
    supplier = fields.get("supplier_name", fields.get("company", ""))
    inv_num = fields.get("invoice_number", fields.get("rst_number", ""))
    lines = ["*" + label + "* — " + confidence + " confidence"]
    if inv_num:
        lines.append("No: `" + str(inv_num) + "`")
    if supplier:
        lines.append("From: " + str(supplier))
    if total:
        lines.append("Amount/Wt: *" + str(total) + "*")
    lines.append("")
    lines.append("*Extracted fields:*")
    for k, v in list(fields.items())[:8]:
        lines.append("• " + k.replace("_", " ").title() + ": `" + str(v) + "`")
    if len(fields) > 8:
        lines.append("_...and " + str(len(fields)-8) + " more fields_")
    lines.append("")
    lines.append("✅ Saved to Google Sheets" if saved else "⚠️ Could not save to Sheets")
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
        print("Process error:", str(e), flush=True)
        send_message(chat_id, "❌ Error: " + str(e))


@app.route("/webhook", methods=["POST"])
def webhook():
    print("Webhook received!", flush=True)
    try:
        data = request.json
        print("Data:", json.dumps(data)[:200], flush=True)
        if not data:
            return "ok"
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        from_user = message.get("from", {})
        from_name = (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
        from_name = from_name or from_user.get("username", "Unknown")

        print("Chat ID:", chat_id, "From:", from_name, flush=True)

        if "photo" in message:
            print("Photo received", flush=True)
            file_id = message["photo"][-1]["file_id"]
            process_image(chat_id, file_id, from_name)
        elif "document" in message:
            doc = message["document"]
            mime = doc.get("mime_type", "")
            if mime.startswith("image/"):
                process_image(chat_id, doc["file_id"], from_name)
            else:
                send_message(chat_id, "⚠️ Please send images only (JPG/PNG).")
        elif "text" in message:
            text = message["text"].strip()
            print("Text:", text, flush=True)
            if text == "/start":
                send_message(chat_id,
                    "*Nalanda Doc Scanner Bot* 📄\n\n"
                    "Send me any business document photo and I will:\n"
                    "• Identify the document type\n"
                    "• Extract all fields\n"
                    "• Save to Google Sheets automatically\n\n"
                    "Just send a photo to get started! 📷"
                )
            elif text == "/help":
                send_message(chat_id,
                    "*Commands:*\n/start — Welcome\n/help — Help\n/status — Status"
                )
            elif text == "/status":
                send_message(chat_id,
                    "*Bot Status*\n"
                    "Claude API: " + ("✅" if CLAUDE_API_KEY else "❌") + "\n"
                    "Google Sheets: " + ("✅" if APPS_SCRIPT_URL else "❌") + "\n"
                    "Sheet prefix: " + SHEET_PREFIX
                )
    except Exception as e:
        print("Webhook error:", str(e), flush=True)
    return "ok"


@app.route("/", methods=["GET"])
def home():
    return "Nalanda Bot is running", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
