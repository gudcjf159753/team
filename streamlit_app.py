import base64
import csv
import io
import json
import os
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import streamlit as st
from openai import APIError, OpenAI
from PIL import Image, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


APP_DIR = Path(__file__).resolve().parent
RAW_DATA_PATH = APP_DIR / "샘플 raw 데이터.csv"


def digits(value):
    return re.sub(r"\D", "", value or "")


def amount(value):
    try:
        return int((value or "0").replace(",", ""))
    except ValueError:
        return 0


def won(value):
    return f"{amount(str(value)):,}"


@st.cache_data
def load_raw_data():
    with RAW_DATA_PATH.open(encoding="utf-8-sig", newline="") as raw_file:
        return list(csv.DictReader(raw_file))


RAW_DATA = load_raw_data()


def read_json(text):
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
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
    return list(matched.values())


def summarize(records):
    if not records:
        return {"company": None, "period": "원시 데이터와 일치하는 거래가 없습니다.", "products": [], "details": []}
    first = records[0]
    groups = defaultdict(list)
    for row in records:
        groups[row["거래구분"]].append(row)
    products = [{
        "category": category,
        "count": len(rows),
        "limit": sum(amount(row["한도금액(원)"]) for row in rows),
        "used": sum(amount(row["사용금액(원)"]) for row in rows),
        "balance": sum(amount(row["잔액(원)"]) for row in rows),
        "loan": sum(amount(row["대출금액(원)"]) for row in rows),
        "delinquent_count": sum(amount(row["연체금액(원)"]) > 0 for row in rows),
    } for category, rows in sorted(groups.items())]
    dates = sorted(row["거래일자"] for row in records if row["거래일자"])
    card_delinquencies = [row for row in records if row["거래구분"] == "카드거래" and amount(row["연체금액(원)"]) > 0]
    details = [{
        "number": index + 1,
        "product": row["상품명"],
        "account": row["계좌번호/카드번호"],
        "limit": amount(row["한도금액(원)"]),
        "used": amount(row["사용금액(원)"]),
        "balance": amount(row["잔액(원)"]),
        "loan": amount(row["대출금액(원)"]),
        "delinquent": amount(row["연체금액(원)"]),
        "date": row["거래일자"],
    } for index, row in enumerate(card_delinquencies)]
    return {
        "company": {
            "name": first["기업명"], "business_number": first["사업자등록번호"],
            "representative": first["대표자명"], "corporate_number": first["법인등록번호"],
            "address": first["본점주소"],
        },
        "period": f"{dates[0]} ~ {dates[-1]}" if dates else "거래일자 정보 없음",
        "products": products,
        "details": details,
    }


def api_key():
    try:
        return st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    except FileNotFoundError:
        return os.getenv("OPENAI_API_KEY")


def analyze_images(uploaded_files):
    key = api_key()
    if not key:
        raise ValueError("Streamlit Secrets에 OPENAI_API_KEY를 설정하세요.")
    prompt = """업로드된 이미지가 대한민국 사업자등록증인지 구조를 확인하세요.
사업자등록번호와 법인등록번호를 이미지에서 보이는 그대로 추출하세요. 보이지 않으면 null로 설정하세요.
추측으로 번호를 만들지 말고 아래 JSON 객체만 반환하세요.
{"is_business_registration_certificate":true,"business_registration_number":"번호 또는 null","corporate_registration_number":"번호 또는 null"}"""
    client = OpenAI(api_key=key)
    reports_by_business = {}
    for index, uploaded in enumerate(uploaded_files):
        try:
            response = client.responses.create(
                model="gpt-6-luna",
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_as_data_url(uploaded.getvalue()), "detail": "high"},
                ]}],
            )
            analyzed = read_json(response.output_text)
        except (APIError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{uploaded.name}: 분석에 실패했습니다. {error}") from error
        business = (analyzed.get("business_registration_number") or "").strip() or "미확인"
        corporate = (analyzed.get("corporate_registration_number") or "").strip() or "미확인"
        report_key = digits(business) or f"unknown-{index}"
        reports_by_business.setdefault(report_key, {
            "business_number": business,
            "report": summarize(find_matches(business, corporate)),
        })
    return list(reports_by_business.values())


def product_rows(report):
    return [[item["category"], won(item["count"]), won(item["limit"]), won(item["used"]), won(item["balance"]), won(item["loan"]), won(item["delinquent_count"])] for item in report["products"]]


def detail_rows(report):
    return [[item["number"], item["product"], item["account"], won(item["limit"]), won(item["used"]), won(item["balance"]), won(item["loan"]), won(item["delinquent"]), item["date"]] for item in report["details"]]


def make_pdf(reports):
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    font = "HYSMyeongJo-Medium"
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontName=font, fontSize=16, leading=22, textColor=colors.HexColor("#111111"))
    heading_style = ParagraphStyle("heading", parent=styles["Heading2"], fontName=font, fontSize=11, leading=16, textColor=colors.HexColor("#111111"))
    text_style = ParagraphStyle("text", parent=styles["BodyText"], fontName=font, fontSize=8, leading=12)
    story = []
    for page, entry in enumerate(reports):
        if page:
            story.append(PageBreak())
        report, company = entry["report"], entry["report"]["company"] or {}
        story += [Paragraph("금융거래확인서 자동화 | 업체별 분석", title_style), Spacer(1, 5 * mm)]
        basic = [["기업명", company.get("name", "미확인")], ["사업자등록번호", company.get("business_number", entry["business_number"])], ["대표자명", company.get("representative", "미확인")], ["법인등록번호", company.get("corporate_number", "미확인")], ["본점주소", company.get("address", "미확인")], ["거래기간", report["period"]]]
        table = Table(basic, colWidths=[40 * mm, 140 * mm])
        table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F1EF")), ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D8D8D6")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story += [Paragraph("01 업체별 기본정보", heading_style), table, Spacer(1, 5 * mm)]
        products = [["구분", "건수", "한도금액", "사용금액", "잔액", "대출금액", "연체건수"]] + product_rows(report)
        product_table = Table(products, repeatRows=1)
        product_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F1EF")), ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D8D8D6")), ("ALIGN", (1, 1), (-1, -1), "RIGHT")]))
        story += [Paragraph("02 금융거래 상품 현황", heading_style), product_table, Spacer(1, 5 * mm)]
        details = [["No.", "상품명", "계좌/카드번호", "한도", "사용", "잔액", "대출", "연체", "거래일자"]] + detail_rows(report)
        if len(details) == 1:
            details.append(["연체금액이 있는 카드거래 내역이 없습니다."] + [""] * 8)
        detail_table = Table(details, repeatRows=1)
        detail_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 6), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F1EF")), ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D8D8D6")), ("ALIGN", (3, 1), (7, -1), "RIGHT")]))
        story += [Paragraph("03 카드거래 연체내역", heading_style), detail_table]
    document.build(story)
    return buffer.getvalue()


def render_report(entry):
    report, company = entry["report"], entry["report"]["company"] or {}
    st.markdown("### 금융거래확인서 자동화 | 업체별 분석")
    info_left, info_right = st.columns(2)
    info = [("기업명", company.get("name", "미확인")), ("사업자등록번호", company.get("business_number", entry["business_number"])), ("대표자명", company.get("representative", "미확인")), ("법인등록번호", company.get("corporate_number", "미확인")), ("본점주소", company.get("address", "미확인")), ("거래기간", report["period"])]
    for index, (label, value) in enumerate(info):
        (info_left if index % 2 == 0 else info_right).markdown(f"**{label}**  \n{value}")
    st.markdown("#### 02 금융거래 상품 현황")
    st.dataframe(product_rows(report), use_container_width=True, hide_index=True, column_config={0: "구분", 1: "건수", 2: "한도금액(원)", 3: "사용금액(원)", 4: "잔액(원)", 5: "대출금액(원)", 6: "연체건수"})
    st.markdown("#### 03 카드거래 연체내역")
    if report["details"]:
        st.dataframe(detail_rows(report), use_container_width=True, hide_index=True, column_config={0: "No.", 1: "상품명", 2: "계좌/카드번호", 3: "한도금액", 4: "사용금액", 5: "잔액", 6: "대출금액", 7: "연체금액", 8: "거래일자"})
    else:
        st.info("연체금액이 있는 카드거래 내역이 없습니다.")


def init_state():
    st.session_state.setdefault("view", "home")
    st.session_state.setdefault("reports", [])
    st.session_state.setdefault("page", 0)


st.set_page_config(page_title="금융거래확인서 자동화", page_icon="▣", layout="wide")
init_state()
st.markdown("""<style>
    .stApp { background:#f7f7f6; color:#111; }
    h1,h2,h3 { letter-spacing:-0.04em; color:#111; }
    div.stButton > button { border-radius:0; border:1px solid #111; background:#111; color:#fff; font-weight:700; }
    div.stButton > button:hover { background:#333; color:#fff; border-color:#333; }
    [data-testid="stFileUploader"] { background:#fff; border:1px dashed #999; padding:1.25rem; }
</style>""", unsafe_allow_html=True)

if st.session_state.view == "home":
    st.title("금융거래확인서 자동화")
    st.caption("AX PoC 기획서와 금융거래 분석 기능을 한 서비스에서 제공합니다.")
    left, right = st.columns(2)
    with left:
        st.subheader("AX PoC 기획서")
        st.write("PoC 목표, 핵심 기능, 사용자 흐름과 검증 항목을 확인합니다.")
        if st.button("기획서 열기", use_container_width=True):
            st.session_state.view = "plan"
            st.rerun()
    with right:
        st.subheader("금융거래확인서 자동화")
        st.write("사업자등록증 이미지를 분석하고 금융거래 결과를 확인합니다.")
        if st.button("분석 도구 열기", use_container_width=True):
            st.session_state.view = "tool"
            st.rerun()

elif st.session_state.view == "plan":
    if st.button("← 메인화면으로"):
        st.session_state.view = "home"
        st.rerun()
    st.title("금융거래확인서 자동화 AX PoC 기획서")
    st.markdown("""### 1. PoC 목표
금융거래확인서 수기 작업에 들어가는 시간을 줄이기 위해, 업로드한 문서의 문자와 숫자를 자동 인식하고 설정된 기준에 따라 거래 내역을 확인한 뒤 결과를 생성할 수 있는지 검증합니다.

### 2. 업무 문제 및 주요 사용자
| 항목 | 내용 |
|---|---|
| 업무 문제 | 금융거래확인서의 내용을 사람이 직접 확인하고 정리하는 수기 작업에 시간이 소요됩니다. |
| 주요 사용자 | 금융거래확인서를 확인하고 결과를 작성하는 업무 담당자 |

### 3. 핵심 기능
이미지 업로드, 문자·숫자 자동 인식, 거래 내역 자동 확인, 결과값 자동 반영과 다운로드를 제공합니다.

### 4. 사용자 이용 흐름
파일 입력 → 자동 처리 → 내역 확인 → 결과 출력

### 5. 입력 데이터와 결과물
사업자등록증 이미지와 원시 데이터를 바탕으로, 사업자별 금융거래 상품 현황과 카드거래 연체내역을 생성합니다.

### 6. 기대 효과
수기 입력·확인 시간을 줄이고, 거래 내역을 구조화해 결과 작성과 출력 업무를 지원합니다.

### 7. 검증 항목
파일 입력, 자동 인식, 자동 확인, 결과 반영, PDF 다운로드와 예외 안내를 확인합니다.""")

else:
    if st.button("← 메인화면으로"):
        st.session_state.view = "home"
        st.rerun()
    st.title("금융거래확인서 자동화")
    st.caption("여러 이미지를 업로드하면 사업자등록번호별 결과를 생성합니다.")
    uploads = st.file_uploader("사업자등록증 이미지 선택", accept_multiple_files=True, type=None, help="읽을 수 있는 이미지 파일은 분석용 PNG로 변환됩니다.")
    if st.button("OpenAI로 분석 시작", disabled=not uploads):
        with st.spinner(f"{len(uploads)}개 이미지를 분석하고 원시 데이터와 매칭하는 중입니다."):
            try:
                st.session_state.reports = analyze_images(uploads)
                st.session_state.page = 0
                st.success(f"{len(uploads)}개 이미지 분석이 완료되었습니다.")
            except ValueError as error:
                st.error(str(error))
    if st.session_state.reports:
        reports = st.session_state.reports
        left, center, right = st.columns([1, 2, 1])
        with left:
            if st.button("이전", disabled=st.session_state.page == 0, use_container_width=True):
                st.session_state.page -= 1
                st.rerun()
        with center:
            st.markdown(f"<p style='text-align:center'><b>{st.session_state.page + 1}페이지 / {len(reports)}페이지</b></p>", unsafe_allow_html=True)
        with right:
            if st.button("다음", disabled=st.session_state.page == len(reports) - 1, use_container_width=True):
                st.session_state.page += 1
                st.rerun()
        render_report(reports[st.session_state.page])
        st.download_button("PDF 다운로드", data=make_pdf(reports), file_name="금융거래확인서-자동화-분석결과.pdf", mime="application/pdf", use_container_width=True)
