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

# ── Sheet names ────────────────────────────────────────
SHEET_NALANDA   = "Nalanda Weighment Slips"
SHEET_DELIVERY  = "Delivery Challans"
SHEET_MR        = "MR Enterprises"
SHEET_BHARATHI  = "Bharathi Invoices"

# ── Headers per sheet ──────────────────────────────────
HEADERS = {
    SHEET_NALANDA: [
        "Scanned At", "Received From",
        "RST No", "Date", "Time In", "Time Out",
        "Vehicle No", "Material",
        "Gross Wt (Kg)", "Tare Wt (Kg)", "Net Wt (Kg)"
    ],
    SHEET_DELIVERY: [
        "Date", "Vehicle No", "Driver Name", "Invoice Number",
        "DC No", "8 Inch", "6 Inch", "4 Inch",
        "Order By", "Phone No", "Area", "Company Name"
    ],
    SHEET_MR: [
        "Scanned At", "Received From",
        "MR Trip No", "Date", "Vehicle No", "Material",
        "MR Gross Wt (Kg)", "MR Tare Wt (Kg)", "MR Net Wt (Kg)",
        "Nalanda Gross Wt (Kg)", "Nalanda Tare Wt (Kg)", "Nalanda Net Wt (Kg)",
        "Gross Diff (Kg)", "Tare Diff (Kg)", "Net Diff (Kg)",
        "Rate", "Amount"
    ],
    SHEET_BHARATHI: [
        "Scanned At", "Received From",
        "Invoice No", "Invoice Date", "Vehicle No", "Material",
        "Bharathi Gross Wt (Kg)", "Bharathi Tare Wt (Kg)", "Bharathi Net Wt (Kg)",
        "Nalanda RST No", "Nalanda Gross Wt (Kg)", "Nalanda Tare Wt (Kg)", "Nalanda Net Wt (Kg)",
        "Difference (Kg)",
        "Basic Price/TO", "Basic Amount", "CGST", "SGST", "Total Amount"
    ],
}

# ── Claude prompt ──────────────────────────────────────
PROMPT = """You are a document scanner for Nalanda Concrete Blocks, Bengaluru.

This image may contain one or more of these document types:
1. NALANDA_WEIGH_SLIP - Nalanda Concrete Blocks weighment slip (RST number, Gross/Tare/Net weights)
2. DELIVERY_CHALLAN - Nalanda Concrete Blocks Delivery Trip Sheet (block delivery to customer)
3. MR_ENTERPRISES - M.R. Enterprises trip sheet (pink form) — may also have a Nalanda weigh slip in same image
4. BHARATHI_INVOICE - Bharathi Rock Products tax invoice/delivery challan — may also have a Nalanda weigh slip in same image

Identify the PRIMARY document type and extract ALL visible fields from ALL documents in the image.

Return ONLY valid JSON, no markdown:
{
  "primary_doc": "BHARATHI_INVOICE",
  "confidence": "HIGH",
  "bharathi": {
    "invoice_number": "",
    "invoice_date": "",
    "vehicle_number": "",
    "material": "",
    "gross_wt_kg": "",
    "tare_wt_kg": "",
    "net_wt_kg": "",
    "basic_price_per_to": "",
    "basic_amount": "",
    "cgst": "",
    "sgst": "",
    "total_amount": ""
  },
  "nalanda_slip": {
    "rst_number": "",
    "date": "",
    "time_in": "",
    "time_out": "",
    "vehicle_number": "",
    "material": "",
    "gross_wt_kg": "",
    "tare_wt_kg": "",
    "net_wt_kg": ""
  },
  "mr_enterprises": {
    "trip_number": "",
    "date": "",
    "vehicle_number": "",
    "material": "",
    "gross_wt_kg": "",
    "tare_wt_kg": "",
    "net_wt_kg": "",
    "rate": "",
    "amount": ""
  },
  "delivery_challan": {
    "trip_sheet_number": "",
    "date": "",
    "vehicle_number": "",
    "driver_name": "",
    "invoice_number": "",
    "qty_8_inch": "",
    "qty_6_inch": "",
    "qty_4_inch": "",
    "order_by": "",
    "phone_number": "",
    "area": "",
    "company_name": ""
  }
}

Only populate sections relevant to what is visible in the image. Leave other sections as empty strings.
For BHARATHI_INVOICE and MR_ENTERPRISES, always check if a Nalanda weigh slip is also present and extract it too."""


def get_gspread_client():
    creds_json = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_sheet(spreadsheet, sheet_name):
    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=sheet_name, rows=2000, cols=30)
        headers = HEADERS.get(sheet_name, ["Scanned At", "Received From"])
        sheet.append_row(headers)
        sheet.format("1:1", {
            "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
            "textFormat": {
                "bold": True,
                "foregroundColor": {"red": 1, "green": 1, "blue": 1}
            }
        })
        print("Created sheet:", sheet_name, flush=True)
    return sheet


def safe(d, key):
    return str(d.get(key, "") or "")


def send_message(chat_id, text, parse_mode="Markdown"):
    try:
        requests.post(TELEGRAM_API + "/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }, timeout=10)
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
            "max_tokens": 1500,
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


def calc_diff(val1, val2):
    try:
        return str(int(float(val1)) - int(float(val2)))
    except:
        return ""


def save_to_sheets(extracted, from_name):
    if not SPREADSHEET_ID or not GOOGLE_CREDS:
        return []
    try:
        gc = get_gspread_client()
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        saved_sheets = []
        doc_type = extracted.get("primary_doc", "")

        b  = extracted.get("bharathi", {})
        n  = extracted.get("nalanda_slip", {})
        mr = extracted.get("mr_enterprises", {})
        dc = extracted.get("delivery_challan", {})

        # ── NALANDA WEIGH SLIP only ────────────────────
        if doc_type == "NALANDA_WEIGH_SLIP":
            sheet = get_or_create_sheet(spreadsheet, SHEET_NALANDA)
            row = [
                ts, from_name,
                safe(n, "rst_number"), safe(n, "date"),
                safe(n, "time_in"), safe(n, "time_out"),
                safe(n, "vehicle_number"), safe(n, "material"),
                safe(n, "gross_wt_kg"), safe(n, "tare_wt_kg"), safe(n, "net_wt_kg")
            ]
            sheet.append_row(row)
            saved_sheets.append(SHEET_NALANDA)

        # ── DELIVERY CHALLAN ───────────────────────────
        elif doc_type == "DELIVERY_CHALLAN":
            sheet = get_or_create_sheet(spreadsheet, SHEET_DELIVERY)
            raw_date = safe(dc, "date")
            try:
                from datetime import datetime as dt
                parsed = dt.strptime(raw_date.strip(), "%d/%m/%Y")
                short_date = parsed.strftime("%-d-%b")
            except:
                short_date = raw_date
            row = [
                short_date,
                safe(dc, "vehicle_number"), safe(dc, "driver_name"),
                safe(dc, "invoice_number"),
                safe(dc, "trip_sheet_number"),
                safe(dc, "qty_8_inch"), safe(dc, "qty_6_inch"), safe(dc, "qty_4_inch"),
                safe(dc, "order_by"), safe(dc, "phone_number"),
                safe(dc, "area"), safe(dc, "company_name")
            ]
            sheet.append_row(row)
            saved_sheets.append(SHEET_DELIVERY)

        # ── MR ENTERPRISES + Nalanda slip ─────────────
        elif doc_type == "MR_ENTERPRISES":
            sheet = get_or_create_sheet(spreadsheet, SHEET_MR)
            mr_gross  = safe(mr, "gross_wt_kg")
            mr_tare   = safe(mr, "tare_wt_kg")
            mr_net    = safe(mr, "net_wt_kg")
            nal_gross = safe(n, "gross_wt_kg")
            nal_tare  = safe(n, "tare_wt_kg")
            nal_net   = safe(n, "net_wt_kg")
            row = [
                ts, from_name,
                safe(mr, "trip_number"), safe(mr, "date"),
                safe(mr, "vehicle_number"), safe(mr, "material"),
                mr_gross, mr_tare, mr_net,
                nal_gross, nal_tare, nal_net,
                calc_diff(mr_gross, nal_gross),
                calc_diff(mr_tare, nal_tare),
                calc_diff(mr_net, nal_net),
                safe(mr, "rate"), safe(mr, "amount")
            ]
            sheet.append_row(row)
            saved_sheets.append(SHEET_MR)

        # ── BHARATHI INVOICE + Nalanda slip ───────────
        elif doc_type == "BHARATHI_INVOICE":
            sheet = get_or_create_sheet(spreadsheet, SHEET_BHARATHI)
            bh_net  = safe(b, "net_wt_kg")
            nal_net = safe(n, "net_wt_kg")
            diff    = calc_diff(bh_net, nal_net)
            row = [
                ts, from_name,
                safe(b, "invoice_number"), safe(b, "invoice_date"),
                safe(b, "vehicle_number"), safe(b, "material"),
                safe(b, "gross_wt_kg"), safe(b, "tare_wt_kg"), bh_net,
                safe(n, "rst_number"),
                safe(n, "gross_wt_kg"), safe(n, "tare_wt_kg"), nal_net,
                diff,
                safe(b, "basic_price_per_to"), safe(b, "basic_amount"),
                safe(b, "cgst"), safe(b, "sgst"), safe(b, "total_amount")
            ]
            sheet.append_row(row)
            saved_sheets.append(SHEET_BHARATHI)

        print("Saved to:", saved_sheets, flush=True)
        return saved_sheets

    except Exception as e:
        print("Sheets error:", str(e), flush=True)
        return []


def format_reply(extracted, saved_sheets):
    if saved_sheets:
        return "✅ Saved to *" + ", ".join(saved_sheets) + "*"
    return "⚠️ Could not save to Sheets — check logs"

def process_image(chat_id, file_id, from_name):
    send_message(chat_id, "⏳ Reading document...")
    try:
        file_url = get_file_url(file_id)
        if not file_url:
            send_message(chat_id, "❌ Could not download image.")
            return
        image_b64 = download_image(file_url)
        extracted = analyse_with_claude(image_b64)
        print("Extracted:", json.dumps(extracted)[:300], flush=True)
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
        from_name = (from_user.get("first_name","") + " " + from_user.get("last_name","")).strip()
        from_name = from_name or from_user.get("username", "Unknown")

        # Whitelist check
        allowed = os.environ.get("ALLOWED_IDS", "")
        if allowed:
            allowed_ids = [x.strip() for x in allowed.split(",")]
            if str(from_user.get("id", "")) not in allowed_ids:
                send_message(chat_id, "⛔ Access denied. Contact Nalanda admin to get access.")
                return "ok"

        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            process_image(chat_id, file_id, from_name)
        elif "document" in message:
            doc = message["document"]
            if doc.get("mime_type","").startswith("image/"):
                process_image(chat_id, doc["file_id"], from_name)
            else:
                send_message(chat_id, "⚠️ Please send images only (JPG/PNG).")
        elif "text" in message:
            text = message["text"].strip()
            if text == "/start":
                send_message(chat_id,
                    "*Nalanda Doc Scanner Bot* 📄\n\n"
                    "Send me any document photo:\n"
                    "• 📋 Bharathi Tax Invoice\n"
                    "• ⚖️ Nalanda Weighment Slip\n"
                    "• 🚛 Delivery Challan\n"
                    "• 📝 MR Enterprises Trip Sheet\n\n"
                    "I extract all fields and save to the correct sheet automatically!\n"
                    "For Bharathi and MR invoices, include the Nalanda slip in the same photo for automatic difference calculation. 📊"
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
