import os
import json
import base64
import requests
import gspread
from flask import Flask, request
from google.oauth2.service_account import Credentials
from datetime import datetime

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDS   = os.environ.get("GOOGLE_CREDS", "")

TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_TOKEN

print("Bot starting...", flush=True)
print("TOKEN set:", bool(TELEGRAM_TOKEN), flush=True)
print("CLAUDE set:", bool(CLAUDE_API_KEY), flush=True)
print("SHEETS set:", bool(SPREADSHEET_ID), flush=True)
print("CREDS set:", bool(GOOGLE_CREDS), flush=True)

# Sheet routing by doc type
SHEET_MAP = {
    "DELIVERY_CHALLAN":  "Delivery Challans",
    "WEIGH_SLIP":        "Weighment Slips",
    "TAX_INVOICE":       "Bharathi Invoices",
    "PURCHASE_INVOICE":  "Bharathi Invoices",
    "SALES_INVOICE":     "Bharathi Invoices",
    "RECEIPT":           "Receipts",
    "PURCHASE_ORDER":    "Purchase Orders",
    "CREDIT_NOTE":       "Bharathi Invoices",
    "DEBIT_NOTE":        "Bharathi Invoices",
    "OTHER":             "Other Documents",
}

# Column headers per sheet
SHEET_HEADERS = {
    "Delivery Challans": [
        "Scanned At", "Received From", "Confidence",
        "Invoice Number", "Invoice Date", "Supplier Name",
        "Supplier GSTIN", "Buyer Name", "Vehicle Number",
        "Material", "Quantity TO", "Unit Price",
        "Basic Amount", "CGST", "SGST", "Total Amount"
    ],
    "Weighment Slips": [
        "Scanned At", "Received From", "Confidence",
        "RST Number", "Date", "Time",
        "Vehicle Number", "Material",
        "Gross Weight Kg", "Tare Weight Kg", "Net Weight Kg"
    ],
    "Bharathi Invoices": [
        "Scanned At", "Received From", "Confidence", "Doc Type",
        "Invoice Number", "Invoice Date", "Supplier Name",
        "Supplier GSTIN", "Buyer Name", "Vehicle Number",
        "Gross Weight Kg", "Tare Weight Kg", "Net Weight Kg",
        "Material", "Quantity TO", "Unit Price",
        "Basic Amount", "CGST", "SGST", "Total Amount"
    ],
}

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
quantity_to, unit_price, basic_amount, cgst, sgst, total_amount, rst_number, date, time.
Return ONLY this JSON: {"doc_type":"TAX_INVOICE","confidence":"HIGH","fields":{"key":"value"}}"""


def get_gspread_client():
    creds_json = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)


def send_message(chat_id, text, parse_mode="Markdown"):
    print("Sending to:", chat_id, flush=True)
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
    return "https://api.telegram.org/file/bot" + TELEGRAM_TOKEN + "/" + data["result"]["file_path"]


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


def save_to_sheets(extracted, from_name):
    if not SPREADSHEET_ID or not GOOGLE_CREDS:
        print("Sheets not configured", flush=True)
        return False
    try:
        gc = get_gspread_client()
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        doc_type = extracted.get("doc_type", "OTHER")
        sheet_name = SHEET_MAP.get(doc_type, "Other Documents")
        fields = extracted.get("fields", {})
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")

        # Get or create the sheet tab
        try:
            sheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=26)
            # Add headers
            headers = SHEET_HEADERS.get(sheet_name, ["Scanned At", "Received From", "Doc Type", "Confidence"])
            sheet.append_row(headers)
            # Format header row
            sheet.format("1:1", {
                "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}
            })

        # Build row based on sheet headers
        headers = SHEET_HEADERS.get(sheet_name, [])
        if headers:
            # Map field names to header names
            field_to_header = {
                "invoice_number": "Invoice Number",
                "invoice_date":   "Invoice Date",
                "supplier_name":  "Supplier Name",
                "supplier_gstin": "Supplier GSTIN",
                "buyer_name":     "Buyer Name",
                "vehicle_number": "Vehicle Number",
                "gross_weight_kg":"Gross Weight Kg",
                "tare_weight_kg": "Tare Weight Kg",
                "net_weight_kg":  "Net Weight Kg",
                "material":       "Material",
                "quantity_to":    "Quantity TO",
                "unit_price":     "Unit Price",
                "basic_amount":   "Basic Amount",
                "cgst":           "CGST",
                "sgst":           "SGST",
                "total_amount":   "Total Amount",
                "rst_number":     "RST Number",
                "date":           "Date",
                "time":           "Time",
            }
            # Build lookup by header name
            data_map = {
                "Scanned At":    ts,
                "Received From": from_name,
                "Confidence":    extracted.get("confidence", ""),
                "Doc Type":      DOC_TYPES.get(doc_type, doc_type),
            }
            for field_key, header_name in field_to_header.items():
                if field_key in fields:
                    data_map[header_name] = str(fields[field_key])

            row = [data_map.get(h, "") for h in headers]
        else:
            row = [ts, from_name, DOC_TYPES.get(doc_type, doc_type), extracted.get("confidence", "")] + list(fields.values())

        sheet.append_row(row)
        print("Saved to sheet:", sheet_name, flush=True)
        return sheet_name
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
    if saved:
        lines.append("✅ Saved to *" + str(saved) + "* sheet")
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
        print("Process error:", str(e), flush=True)
        send_message(chat_id, "❌ Error: " + str(e))


@app.route("/webhook", methods=["POST"])
def webhook():
    print("Webhook received!", flush=True)
    try:
        data = request.json
        if not data:
            return "ok"
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        from_user = message.get("from", {})
        from_name = (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
        from_name = from_name or from_user.get("username", "Unknown")
        print("From:", from_name, "Chat:", chat_id, flush=True)

        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            process_image(chat_id, file_id, from_name)
        elif "document" in message:
            doc = message["document"]
            if doc.get("mime_type", "").startswith("image/"):
                process_image(chat_id, doc["file_id"], from_name)
            else:
                send_message(chat_id, "⚠️ Please send images only (JPG/PNG).")
        elif "text" in message:
            text = message["text"].strip()
            print("Text:", text, flush=True)
            if text == "/start":
                send_message(chat_id,
                    "*Nalanda Doc Scanner Bot* 📄\n\n"
                    "Send me any document photo:\n"
                    "• Bharathi Tax Invoice\n"
                    "• Delivery Challan\n"
                    "• Weighment Slip\n\n"
                    "I will extract all fields and save to the correct sheet automatically! 📊"
                )
            elif text == "/status":
                send_message(chat_id,
                    "*Bot Status*\n"
                    "Claude API: " + ("✅" if CLAUDE_API_KEY else "❌") + "\n"
                    "Google Sheets: " + ("✅" if SPREADSHEET_ID else "❌") + "\n"
                    "Credentials: " + ("✅" if GOOGLE_CREDS else "❌")
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
