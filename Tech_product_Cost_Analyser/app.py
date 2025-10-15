from flask import Flask, render_template, request, redirect, url_for, jsonify
import google.generativeai as genai
import os
import json
import re
import requests
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    logging.warning("GOOGLE_API_KEY not set; generative calls will fail without it.")
else:
    genai.configure(api_key=GOOGLE_API_KEY)

MODEL_NAME = os.getenv("MODEL_NAME", "models/gemini-2.5-flash")

CURRENCY_MAP = {
    "india": "INR", "ind": "INR", "saudi": "SAR", "saudi arabia": "SAR", "kingdom": "SAR",
    "uae": "AED", "united arab emirates": "AED", "united states": "USD", "usa": "USD",
    "us": "USD", "america": "USD", "canada": "CAD", "australia": "AUD", "uk": "GBP",
    "united kingdom": "GBP", "germany": "EUR", "france": "EUR", "europe": "EUR",
    "japan": "JPY", "china": "CNY", "pakistan": "PKR", "bahrain": "BHD", "qatar": "QAR",
    "oman": "OMR",
}

def detect_currency_code(place):
    if not place:
        return "USD"
    p = place.strip().lower()
    for k, v in CURRENCY_MAP.items():
        if k in p:
            return v
    return "USD"

def response_to_text(resp):
    try:
        if hasattr(resp, "text") and resp.text:
            return resp.text
    except Exception:
        pass
    try:
        return resp.candidates[0].content.parts[0].text
    except Exception:
        pass
    try:
        return resp.candidates[0].content[0].text
    except Exception:
        pass
    return str(resp)

def extract_json_from_text(text):
    if not text:
        return None
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None
    s = m.group()
    try:
        return json.loads(s)
    except Exception:
        try:
            s2 = s.replace("'", '"')
            s2 = re.sub(r',\s*}', '}', s2)
            s2 = re.sub(r',\s*\]', ']', s2)
            return json.loads(s2)
        except Exception:
            return None

def parse_number(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s == "":
        return 0.0
    range_match = re.match(r'.*?(\d[\d,\.]*)\s*[-–]\s*(\d[\d,\.]*).*', s)
    if range_match:
        a = float(range_match.group(1).replace(',', ''))
        b = float(range_match.group(2).replace(',', ''))
        return round((a + b) / 2.0, 2)
    cleaned = re.sub(r'[^\d\.,]', '', s)
    if cleaned == "":
        return 0.0
    if cleaned.count('.') > 1:
        cleaned = cleaned.replace(',', '')
    try:
        num = float(cleaned.replace(',', ''))
        return round(num, 2)
    except Exception:
        return 0.0

# --- NEW: structured schema asking model to return 5 named categories ---
CATEGORY_KEYS = [
    ("vision_2030", "Vision 2030 & Global Event Diplomacy"),
    ("financial_modeling", "Financial Modeling"),
    ("government_relations", "Government Relations"),
    ("global_case_stories", "Global Case Stories"),
    ("hybrid_learning", "Hybrid Learning")
]

def build_structured_prompt(product_name, place, currency_code):
    categories_schema = ",\n  ".join(
        f'"{k}": {{ "items": [{{"name":"string","specs":"string","quantity":1,"price":0}}], "subtotal": 0 }}'
        for k,_ in CATEGORY_KEYS
    )
    prompt = f"""
You are a helpful assistant. Provide EXACTLY one JSON object (no extra text) that follows this schema:
{{
  "product": "string",
  "currency": "{currency_code}",
  {categories_schema},
  "grand_total": 0
}}

Task: For product "{product_name}" and market "{place}", produce a breakdown for each of the five strategic categories:
- Vision 2030 & Global Event Diplomacy -> key localization, branding, partnerships (vision_2030)
- Financial Modeling -> hardware, software, installation, testing (financial_modeling)
- Government Relations -> legal, licensing, permits, compliance (government_relations)
- Global Case Stories -> research, imports, R&D, consultancy (global_case_stories)
- Hybrid Learning -> cloud, QR, AR/AI, analytics (hybrid_learning)

For each category list items (name, short specs, integer quantity, approximate price in {currency_code}) and provide an approximate numeric subtotal for the category. Finally set "grand_total" equal to the sum of the category subtotals.

Important:
- RETURN ONLY THE JSON OBJECT (no commentary).
- Prices should be realistic approximate numeric values (no currency symbols inside numbers).
"""
    return prompt

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/autocomplete", methods=["GET"])
def autocomplete():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        resp = requests.get(f"https://restcountries.com/v3.1/name/{requests.utils.quote(q)}?fields=name,cca2", timeout=6)
        if resp.status_code == 200:
            arr = resp.json()
            results = []
            seen = set()
            for c in arr:
                name = c.get("name", {}).get("common")
                cc = c.get("cca2", "")
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    results.append({"display_name": name, "country_code": cc})
            return jsonify(results[:12])
    except Exception:
        pass
    try:
        resp = requests.get("https://restcountries.com/v3.1/all?fields=name,cca2", timeout=6)
        arr = resp.json()
        qlow = q.lower()
        filtered = []
        for c in arr:
            name = c.get("name", {}).get("common")
            cc = c.get("cca2", "")
            if not name:
                continue
            if qlow in name.lower():
                filtered.append({"display_name": name, "country_code": cc})
        filtered.sort(key=lambda x: (0 if x["display_name"].lower().startswith(qlow) else 1, x["display_name"]))
        return jsonify(filtered[:12])
    except Exception:
        return jsonify([])

@app.route("/results", methods=["POST"])
def results():
    product_name = request.form.get("product_name", "").strip() or request.form.get("product", "").strip()
    place = request.form.get("place", "").strip()
    currency_code = detect_currency_code(place)

    prompt = build_structured_prompt(product_name, place, currency_code)

    try:
        if not GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY is not configured in the environment.")

        model = genai.GenerativeModel(MODEL_NAME)
        resp = model.generate_content(prompt)
        text = response_to_text(resp)
        data = extract_json_from_text(text)

        # If not valid structured JSON, try a follow-up instruction to convert
        if not data:
            followup = (
                "You returned text that wasn't valid JSON following the schema. Convert your previous content to EXACT JSON matching the schema and nothing else.\n\n"
                f"Previous output:\n{text}\n\nSchema example snippet:\n"
                '{' + f'"product":"string","currency":"{currency_code}", ... "grand_total":0' + '}'
            )
            resp2 = model.generate_content(followup)
            text2 = response_to_text(resp2)
            data = extract_json_from_text(text2)

        # Final fallback: if still no JSON, attempt to interpret older 'items' format
        if not data:
            # attempt to parse older style: items list with price entries
            fallback = {
                "product": product_name,
                "currency": currency_code,
                "vision_2030": {"items": [], "subtotal": 0},
                "financial_modeling": {"items": [], "subtotal": 0},
                "government_relations": {"items": [], "subtotal": 0},
                "global_case_stories": {"items": [], "subtotal": 0},
                "hybrid_learning": {"items": [], "subtotal": 0},
                "grand_total": 0
            }
            data = fallback

        # Normalize categories into unified structure
        categories = []
        grand_total = 0.0
        for key, pretty in CATEGORY_KEYS:
            cat_block = data.get(key, {})
            items = cat_block.get("items", []) if isinstance(cat_block, dict) else []
            subtotal = cat_block.get("subtotal", 0) if isinstance(cat_block, dict) else 0

            normalized_items = []
            computed_sub = 0.0
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = it.get("name", "") or str(it.get("name", ""))
                specs = it.get("specs", "") or ""
                qty_raw = it.get("quantity", 1)
                try:
                    qty = int(qty_raw)
                    if qty <= 0:
                        qty = 1
                except Exception:
                    qty = 1
                raw_price = it.get("price", 0)
                price_num = parse_number(raw_price)
                computed_sub += price_num * qty
                normalized_items.append({
                    "name": name,
                    "specs": specs,
                    "quantity": qty,
                    "price": round(price_num, 2)
                })

            # If model provided a numeric subtotal, trust it; otherwise use computed_sub
            try:
                subtotal_num = float(subtotal)
                if subtotal_num <= 0:
                    subtotal_num = round(computed_sub, 2)
            except Exception:
                subtotal_num = round(computed_sub, 2)

            grand_total += subtotal_num
            categories.append({
                "key": key,
                "title": pretty,
                "items": normalized_items,
                "subtotal": round(subtotal_num, 2)
            })

        grand_total = round(grand_total, 2)

        # If model returned a grand_total, prefer that (but keep computed grand_total as fallback)
        if isinstance(data.get("grand_total", None), (int, float)) and data.get("grand_total") > 0:
            model_total = parse_number(data.get("grand_total"))
            if abs(model_total - grand_total) > 0:
                # keep both if they differ (pass model_total too)
                pass
            grand_total = round(model_total, 2)

        return render_template(
            "results.html",
            product_name=product_name,
            place=place,
            currency=currency_code,
            categories=categories,
            grand_total=grand_total,
            raw_model_data=data
        )

    except Exception as e:
        fallback = {
            "product": product_name or "Unknown",
            "currency": currency_code,
            "vision_2030": {"items": [{"name": "Error", "specs": str(e), "quantity": 1, "price": 0}], "subtotal": 0},
            "financial_modeling": {"items": [], "subtotal": 0},
            "government_relations": {"items": [], "subtotal": 0},
            "global_case_stories": {"items": [], "subtotal": 0},
            "hybrid_learning": {"items": [], "subtotal": 0},
            "grand_total": 0
        }
        categories_fallback = []
        for key, pretty in CATEGORY_KEYS:
            block = fallback.get(key, {"items": [], "subtotal": 0})
            categories_fallback.append({
                "key": key,
                "title": pretty,
                "items": block.get("items", []),
                "subtotal": block.get("subtotal", 0)
            })
        return render_template(
            "results.html",
            product_name=product_name,
            place=place,
            currency=currency_code,
            categories=categories_fallback,
            grand_total=0.0,
            raw_model_data=fallback,
            error=str(e)
        )

@app.route("/analyze", methods=["POST"])
def analyze_alias():
    return results()

if __name__ == "__main__":
    app.run(debug=True)