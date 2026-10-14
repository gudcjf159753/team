import base64
import csv
import io
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from openai import APIError, OpenAI
from PIL import Image, UnidentifiedImageError

load_dotenv()
APP_DIR = Path(__file__).resolve().parent
RAW_DATA_PATH = APP_DIR / "샘플 raw 데이터.csv"
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

with RAW_DATA_PATH.open(encoding="utf-8-sig", newline="") as raw_file:
    RAW_DATA = list(csv.DictReader(raw_file))


def digits(value):
    return re.sub(r"\D", "", value or "")


def amount(value):
    try:
        return int((value or "0").replace(",", ""))
    except ValueError:
        return 0


def read_json(text):
    clean = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", text.strip(), flags=re.I)
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("분석 결과를 읽을 수 없습니다.")
    return json.loads(clean[start:end + 1])


def image_as_data_url(file_bytes):
    try:
        source = Image.open(io.BytesIO(file_bytes))
        source.seek(0)
        if source.mode not in ("RGB", "RGBA"):
            source = source.convert("RGBA" if "transparency" in source.info else "RGB")
        output = io.BytesIO()
        source.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("읽을 수 없는 이미지 파일입니다. 다른 이미지 파일을 선택하세요.") from error


def find_matches(business_number, corporate_number):
    business, corporate = digits(business_number), digits(corporate_number)
    business_rows = [row for row in RAW_DATA if business and digits(row["사업자등록번호"]) == business]
    corporate_rows = [row for row in RAW_DATA if corporate and digits(row["법인등록번호"]) == corporate]
    matched = {row["거래ID"]: row for row in business_rows + corporate_rows}
    return list(matched.values()), bool(business_rows), bool(corporate_rows)


def summarize(records):
    if not records:
        return {"company": None, "period": "원시 데이터와 일치하는 거래가 없습니다.", "products": [], "details": []}
    first = records[0]
    groups = defaultdict(list)
    for row in records:
        groups[row["거래구분"]].append(row)
    products = [{
        "category": category, "count": len(rows),
        "limit": sum(amount(row["한도금액(원)"]) for row in rows),
        "used": sum(amount(row["사용금액(원)"]) for row in rows),
        "balance": sum(amount(row["잔액(원)"]) for row in rows),
        "loan": sum(amount(row["대출금액(원)"]) for row in rows),
        "delinquent_count": sum(amount(row["연체금액(원)"]) > 0 for row in rows),
    } for category, rows in sorted(groups.items())]
    dates = sorted(row["거래일자"] for row in records if row["거래일자"])
    card_delinquencies = [row for row in records
                          if row["거래구분"] == "카드거래" and amount(row["연체금액(원)"]) > 0]
    details = [{
        "number": index + 1, "category": row["거래구분"], "product": row["상품명"], "account": row["계좌번호/카드번호"],
        "limit": amount(row["한도금액(원)"]), "used": amount(row["사용금액(원)"]),
        "balance": amount(row["잔액(원)"]), "loan": amount(row["대출금액(원)"]),
        "delinquent": amount(row["연체금액(원)"]), "date": row["거래일자"],
    } for index, row in enumerate(card_delinquencies)]
    return {
        "company": {"name": first["기업명"], "business_number": first["사업자등록번호"],
                    "representative": first["대표자명"], "corporate_number": first["법인등록번호"],
                    "address": first["본점주소"], "start_date": first["거래시작일"]},
        "period": f"{dates[0]} ~ {dates[-1]}" if dates else "거래일자 정보 없음",
        "products": products, "details": details,
    }


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "financial-transaction-certificate-ax-poc-plan_integrated.html")


@app.post("/api/analyze")
def analyze():
    if not os.getenv("OPENAI_API_KEY"):
        return jsonify(error=".env 파일에 OPENAI_API_KEY를 설정하세요."), 500
    uploads = [uploaded for uploaded in request.files.getlist("image") if uploaded.filename]
    if not uploads:
        return jsonify(error="분석할 이미지 파일을 선택하세요."), 400
    prompt = """업로드된 이미지가 대한민국 사업자등록증인지 구조를 확인하세요.
사업자등록번호와 법인등록번호를 이미지에서 보이는 그대로 추출하세요. 보이지 않으면 null로 설정하세요.
추측으로 번호를 만들지 말고 아래 JSON 객체만 반환하세요.
{"is_business_registration_certificate":true,"structure_message":"구조 확인 결과","business_registration_number":"번호 또는 null","corporate_registration_number":"번호 또는 null","summary":"한 문장 요약"}"""
    reports_by_business = {}
    client = OpenAI()
    for index, uploaded in enumerate(uploads):
        try:
            image_url = image_as_data_url(uploaded.read())
        except ValueError as error:
            return jsonify(error=f"{uploaded.filename}: {error}"), 400
        try:
            response = client.responses.create(
                model="gpt-6-luna",
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ]}],
            )
            analyzed = read_json(response.output_text)
        except (APIError, ValueError, json.JSONDecodeError) as error:
            return jsonify(error=f"{uploaded.filename}: OpenAI 분석에 실패했습니다: {error}"), 502
        except Exception:
            return jsonify(error=f"{uploaded.filename}: 분석 중 문제가 발생했습니다. API 키와 이미지 파일을 확인한 뒤 다시 시도하세요."), 500
        business = (analyzed.get("business_registration_number") or "").strip() or "미확인"
        corporate = (analyzed.get("corporate_registration_number") or "").strip() or "미확인"
        records, _, _ = find_matches(business, corporate)
        # 같은 사업자등록번호가 여러 이미지에서 나와도 결과 페이지는 한 장만 만듭니다.
        report_key = digits(business) or f"unknown-{index}"
        reports_by_business.setdefault(report_key, {
            "business_number": business,
            "report": summarize(records),
        })
    return jsonify({"reports": list(reports_by_business.values()), "analyzed_file_count": len(uploads)})


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="업로드한 이미지 파일의 전체 크기는 10MB 이하이어야 합니다."), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
