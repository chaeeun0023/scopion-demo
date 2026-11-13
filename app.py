"""SCOPION - Streamlit 데모 앱 (업로드 + AI 추출 + 규칙기반 검증 + 승인)
공통스키마 v2.2 구조를 반영한 버전입니다. (모르는 값은 "UNKNOWN" 대신 null)

실행법: 터미널(cmd)에서 이 파일이 있는 폴더로 이동한 뒤
    streamlit run app.py
를 입력하면 브라우저가 자동으로 열립니다.
"""
import json
import os
import uuid
import base64
import io
import datetime
import urllib.request
import urllib.error
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    HAS_MATPLOTLIB = True

    # 한글 폰트 설정 (없으면 '월' 같은 글자가 네모(tofu)로 깨져서 보임)
    _KOREAN_FONT_CANDIDATES = [
        "Malgun Gothic", "AppleGothic", "NanumGothic", "NanumBarunGothic",
        "Noto Sans CJK KR", "Noto Sans KR", "Apple SD Gothic Neo",
    ]
    _available_fonts = {f.name for f in fm.fontManager.ttflist}
    for _name in _KOREAN_FONT_CANDIDATES:
        if _name in _available_fonts:
            plt.rcParams["font.family"] = _name
            break
    plt.rcParams["axes.unicode_minus"] = False
except ImportError:
    HAS_MATPLOTLIB = False

import pandas as pd
import streamlit as st
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------- 공통 설정: 요금 항목 코드 -> group (2_항목설명.md 기준) ----------

CHARGE_CODE_GROUP = {
    "base_fee": "supply",
    "usage_fee": "supply",
    "climate_env_fee": "supply",
    "fuel_cost_adjustment": "supply",
    "power_factor_adjustment": "supply",
    "discount": "supply",
    "meter_replacement_fee": "supply",
    "special_meter_mgmt_fee": "supply",
    "vat": "vat",
    "power_fund": "other",
    "rounding": "other",
    "tv_fee": "other",
    "late_fee": "other",
    "unpaid_carryover": "other",
    "adjustment": "other",
    "other": "other",
}
CHARGE_CODE_OPTIONS = list(CHARGE_CODE_GROUP.keys())

CHARGE_CODE_LABEL = {
    "base_fee": "기본요금", "usage_fee": "전력량/사용요금", "climate_env_fee": "기후환경요금",
    "fuel_cost_adjustment": "연료비조정액", "power_factor_adjustment": "역률요금",
    "discount": "경감할인/감면", "meter_replacement_fee": "계량기(GM) 교체비",
    "special_meter_mgmt_fee": "특수GM관리비", "vat": "부가세",
    "power_fund": "전력기금", "rounding": "원단위절사", "tv_fee": "TV수신료",
    "late_fee": "연체료", "unpaid_carryover": "미납요금", "adjustment": "정산/기타조정",
    "other": "기타",
}

# document 공통 필드 (라벨)
DOCUMENT_FIELDS = [
    ("billing_month", "청구월 (YYYY-MM)"),
    ("period_start", "사용기간 시작"),
    ("period_end", "사용기간 종료"),
    ("customer_number", "고객번호"),
    ("site_address", "사용장소/사용주소"),
    ("meter_number", "계기(계량기)번호"),
    ("billing_amount", "청구금액(원)"),
    ("tax_supply_amount", "공급가액(원)"),
    ("issue_date", "작성일자/발행일"),
    ("supplier_reg_number", "공급자등록번호"),
    ("recipient_reg_number", "공급받는자등록번호"),
    ("supplier_name", "공급자명"),
    ("payment_due_date", "납기일/납부마감일"),
]
DOCUMENT_REQUIRED = {"billing_month", "period_start", "period_end", "customer_number", "billing_amount"}

ACTIVITY_FIELDS = [
    ("value", "사용량"),
    ("unit", "단위"),
    ("period_start", "검침 시작"),
    ("period_end", "검침 종료"),
    ("meter_current", "당월지침"),
    ("meter_previous", "전월지침"),
    ("usage_prev_month", "전월 사용량"),
    ("usage_same_month_lastyear", "전년동월 사용량"),
]
ACTIVITY_REQUIRED = {"value", "unit", "period_start", "period_end"}

DETAIL_FIELDS = {
    "electricity": [
        ("contract_type", "계약종별"),
        ("contract_power_kw", "계약전력(kW)"),
        ("meter_multiplier", "계기배수"),
        ("approval_number", "전자세금계산서 승인번호"),
        ("regular_reading_day", "정기검침일(매월 N일)"),
        ("power_factor_lagging", "지상역률(%)"),
        ("power_factor_leading", "진상역률(%)"),
    ],
    "gas": [
        ("correction_factor", "온압보정계수"),
        ("corrected_volume", "보정사용량"),
        ("corrected_volume_unit", "보정사용량 단위"),
        ("avg_calorific_value", "평균열량"),
        ("avg_calorific_unit", "평균열량 단위"),
        ("total_heat_value", "사용열량"),
        ("total_heat_unit", "사용열량 단위"),
        ("usage_type", "용도"),
        ("reading_method", "검침근거"),
        ("unit_price", "단가"),
        ("unit_price_unit", "단가 단위"),
        ("e_payment_number", "전자납부번호"),
        ("monthly_usage_history", "최근 1년 사용량 추이"),
        ("supply_point_number", "수용가번호"),
    ],
}
DETAIL_REQUIRED = {
    "electricity": {"contract_type", "contract_power_kw", "meter_multiplier"},
    "gas": {"correction_factor", "corrected_volume", "avg_calorific_value", "total_heat_value", "usage_type"},
}

FACTORS_PATH = os.path.join(HERE, "emission_factors.json")


# ---------- 배출계수 (연도·연료별) ----------

def load_factors():
    """emission_factors.json을 읽어온다. 파일이 없으면 빈 틀을 돌려준다."""
    if os.path.exists(FACTORS_PATH):
        with open(FACTORS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("electricity", [])
    data.setdefault("gas", [])
    return data


def save_factors(data):
    with open(FACTORS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 반영된 산정 결과 누적 기록 (사업장별·월별 집계용) ----------

HISTORY_PATH = os.path.join(HERE, "emission_history.json")


def load_history():
    """emission_history.json을 읽어온다. 고지서 1건씩 '반영'될 때마다 쌓이는 누적 기록."""
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("records", [])
    return data


def save_history(data):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_history_record(confirmed, calc, doc_type, site):
    """승인된 문서 1건의 산정 결과를 누적 기록에 추가한다 (같은 document_id는 교체해 중복 집계 방지)."""
    history = load_history()
    billing_month = (confirmed.get("document") or {}).get("billing_month")
    entry = {
        "document_id": confirmed.get("document_id"),
        "site": site or "미지정",
        "billing_month": billing_month,
        "scope": 1 if doc_type == "gas" else 2,
        "doc_type": doc_type,
        "result_tco2e": calc["result_tco2e"],
        "recorded_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    history["records"] = [r for r in history["records"] if r.get("document_id") != entry["document_id"]]
    history["records"].append(entry)
    save_history(history)
    return history


def get_factor(factors, fuel, year):
    """해당 연료·연도의 배출계수 행을 찾는다. 없으면 None (= 산정하지 않음)."""
    if year is None:
        return None
    for row in factors.get(fuel, []):
        if row.get("year") == year:
            return row
    return None


def compute_emission(record, factors):
    """승인된 레코드에 배출계수를 곱해 배출량(tCO2e)을 계산한다.
    계수가 없으면 계산하지 않고 이유를 돌려준다 ('계수 없으면 산정하지 않음' 원칙)."""
    doc_type = "electricity" if record.get("document_type") == "electricity_bill" else "gas"
    billing_month = (record.get("document") or {}).get("billing_month")
    year = None
    if billing_month and "-" in str(billing_month):
        try:
            year = int(str(billing_month).split("-")[0])
        except ValueError:
            year = None

    if year is None:
        return {"ok": False, "reason": "청구월을 알 수 없어 연도별 배출계수를 적용할 수 없습니다."}

    factor = get_factor(factors, doc_type, year)
    if factor is None:
        return {"ok": False, "reason": f"{year}년 {('전력' if doc_type=='electricity' else '도시가스')} 배출계수가 등록되어 있지 않아 산정을 보류합니다."}

    act0 = (record.get("activities") or [{}])[0]
    detail = record.get("detail") or {}

    if doc_type == "electricity":
        usage_kwh = _num(act0.get("value"))
        if usage_kwh is None:
            return {"ok": False, "reason": "사용량(kWh)이 없어 산정할 수 없습니다."}
        usage_mwh = usage_kwh / 1000
        result = usage_mwh * factor["value"]
        formula = f"{usage_kwh:,.0f}kWh ÷ 1000 × {factor['value']}{factor['unit']} = {result:.3f} tCO2e"
        input_snapshot = {"usage_kwh": usage_kwh}
    else:
        heat_mj = _num(detail.get("total_heat_value"))
        if heat_mj is None:
            return {"ok": False, "reason": "사용열량(MJ)이 없어 산정할 수 없습니다. (상세 항목의 '사용열량'을 먼저 채워주세요)"}
        usage_gj = heat_mj / 1000
        result = usage_gj * factor["value"] / 1000  # kgCO2/GJ -> tCO2e
        formula = f"{heat_mj:,.0f}MJ ÷ 1000 × {factor['value']}{factor['unit']} ÷ 1000 = {result:.3f} tCO2e"
        input_snapshot = {"total_heat_value_mj": heat_mj}

    return {
        "ok": True,
        "result_tco2e": result,
        "formula": formula,
        "emission_factor": factor,
        "input_snapshot": input_snapshot,
        "calculated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


# ---------- 기존 run_llm_single.py와 동일한 로직 (재사용) ----------

def get_api_key():
    """반환값이 None이면 st.session_state['_api_key_error']에 Secrets를 읽다가 난 에러(있다면)를 남긴다."""
    st.session_state.pop("_api_key_error", None)
    # Streamlit Cloud에 올렸을 때는 "Secrets"에 저장한 값을 쓴다 (코드/저장소에 키를 넣지 않기 위함).
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            key = st.secrets["ANTHROPIC_API_KEY"]
            if key:
                return key
    except Exception as e:
        # Secrets 자체가 깨져 있으면(TOML 형식 오류 등) 여기서 예외가 나는데, 이걸 숨기지 않고 남겨서
        # "로컬 파일도 못 찾음" 메시지만 보고 원인을 못 찾는 상황을 막는다.
        st.session_state["_api_key_error"] = f"Secrets를 읽는 중 오류: {e}"
    # 내 컴퓨터에서 로컬로 돌릴 때는 기존처럼 anthropic_key.txt 파일에서 읽는다.
    for p in [os.path.join(HERE, 'anthropic_key.txt'), os.path.join(os.path.dirname(HERE), 'anthropic_key.txt')]:
        if os.path.exists(p):
            return open(p, encoding='utf-8').read().strip()
    return None


def build_system_prompt(doc_type):
    """UNKNOWN 문자열 대신 null을 쓰도록 바꾼 v2용 시스템 프롬프트."""
    kind = "전기요금" if doc_type == "electricity" else "도시가스"
    return f"""너는 한국 에너지 고지서({kind})에서 탄소배출량 산정에 필요한 값을 읽어내는 추출기다.
반드시 아래 규칙을 지킨다.

1. 이미지에 실제로 보이는 값만 적는다. 보이지 않거나 흐려서 확신할 수 없는 값은 절대 추측하지 말고 JSON null로 적는다. ("UNKNOWN" 같은 문자열을 쓰지 않는다.)
2. 숫자는 고지서에 적힌 그대로 옮긴다(쉼표 제거는 가능하나 계산·반올림·단위 환산은 하지 않는다).
3. 고객번호·사업자등록번호·승인번호는 한 글자씩 원문 그대로 옮긴다.
4. charges 목록에는 개별 요금 항목만 넣는다. "전기요금계", "당월요금계", "청구금액" 같은 합계/소계 줄은 넣지 않는다.
5. 감면, 할인, 절사처럼 청구금액에서 빼는 항목은 amount를 음수로 적는다.
6. charges[].code는 주어진 enum 중 가장 가까운 것을 고른다. 예: 기본요금→base_fee, 전력량요금/사용요금/사용료→usage_fee,
   기후환경요금→climate_env_fee, 연료비조정액→fuel_cost_adjustment, 역률요금→power_factor_adjustment,
   경감할인/감면금액→discount, GM교체비/계량기교체비→meter_replacement_fee, 특수GM관리비→special_meter_mgmt_fee,
   부가세/부가가치세→vat, 전력기금→power_fund, 원단위절사/절사금액→rounding, TV수신료→tv_fee,
   연체료/미납연체료→late_fee, 미납요금→unpaid_carryover, 정산금액/기타조정→adjustment, 그 외→other.
7. charges[].name_raw에는 고지서에 적힌 항목명을 그대로 적는다.
8. 주어진 JSON 스키마의 모든 키를 빠짐없이 채우고(모르면 null), JSON 외의 다른 말은 출력하지 않는다."""


def encode_image(file_obj, max_side=1568):
    im = Image.open(file_obj).convert('RGB')
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def call_claude(model, schema, system_prompt, img_b64, api_key, temperature=True, forced=True):
    body = {
        "model": model, "max_tokens": 4096, "system": system_prompt,
        "tools": [{"name": "record_bill", "description": "고지서에서 추출한 값을 기록한다", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "record_bill"} if forced else {"type": "auto"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": "첨부한 고지서 이미지에서 값을 추출해 반드시 record_bill 도구를 한 번 호출해 기록해줘."}]}],
    }
    if temperature:
        body["temperature"] = 0
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        if temperature and 'temperature' in msg:
            return call_claude(model, schema, system_prompt, img_b64, api_key, temperature=False, forced=forced)
        if forced and 'tool_choice' in msg:
            return call_claude(model, schema, system_prompt, img_b64, api_key, temperature=temperature, forced=False)
        raise RuntimeError(f"{e.code} {msg[:300]}")


# ---------- v2.2 레코드로 감싸기 ----------

def build_v2_record(doc_type, extracted, model):
    """추출 결과(document/activities/detail/charges)를 공통스키마 v2.2 모양의 레코드로 감싼다."""
    charges = []
    for c in extracted.get("charges") or []:
        code = c.get("code")
        charges.append({
            "code": code,
            "name_raw": c.get("name_raw"),
            "amount": c.get("amount"),
            "group": CHARGE_CODE_GROUP.get(code, "other"),
        })
    record = {
        "schema_version": "record-2.2",
        "document_id": str(uuid.uuid4()),
        "document_type": "electricity_bill" if doc_type == "electricity" else "gas_bill",
        "status": "EXTRACTED",
        "source": {
            "type": "bill_llm",
            "provider": model,
            "evidence_file_id": None,
            "extracted_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        },
        "document": extracted.get("document") or {},
        "activities": extracted.get("activities") or [],
        "detail": extracted.get("detail") or {},
        "charges": charges,
        "field_status": {},
        "validation": {"issues": []},
        "review": {"status": "pending"},
        "history": [{"action": "extracted", "at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}],
        "org_id": None,
        "site_id": None,
        "emissions": None,
    }
    return record


def compute_field_status(record, doc_type):
    """document/activities[0]/detail의 필수 항목이 비어있는지 ok/missing으로 표시한다."""
    status = {}
    doc = record["document"]
    for key in DOCUMENT_REQUIRED:
        status[f"document.{key}"] = "missing" if doc.get(key) in (None, "") else "ok"
    act = (record["activities"] or [{}])[0]
    for key in ACTIVITY_REQUIRED:
        status[f"activities[0].{key}"] = "missing" if act.get(key) in (None, "") else "ok"
    detail = record["detail"]
    for key in DETAIL_REQUIRED.get(doc_type, set()):
        status[f"detail.{key}"] = "missing" if detail.get(key) in (None, "") else "ok"
    return status


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_validation(record):
    """규칙 기반 검산. charges 합이 공급가액/청구금액과 맞는지만 확인한다 (CALC_MISMATCH / CALC_SKIPPED)."""
    issues = []
    charges = record.get("charges") or []
    doc = record.get("document") or {}

    def sum_group(g):
        vals = [c.get("amount") for c in charges if c.get("group") == g]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    supply_sum = sum_group("supply")
    tax_supply = _num(doc.get("tax_supply_amount"))
    if supply_sum is None or tax_supply is None:
        issues.append({"code": "CALC_SKIPPED", "level": "warning", "field": "document.tax_supply_amount",
                        "message": "공급가액 항목 합계 또는 공급가액 값이 없어 검산을 건너뜀"})
    elif abs(supply_sum - tax_supply) > 1:
        issues.append({"code": "CALC_MISMATCH", "level": "error", "field": "document.tax_supply_amount",
                        "message": f"공급(supply) 항목 합계({supply_sum:,.0f}원)가 공급가액({tax_supply:,.0f}원)과 다름"})

    all_vals = [c.get("amount") for c in charges]
    all_sum = sum(v for v in all_vals if v is not None) if any(v is not None for v in all_vals) else None
    billing_amount = _num(doc.get("billing_amount"))
    if all_sum is None or billing_amount is None or any(v is None for v in all_vals):
        issues.append({"code": "CALC_SKIPPED", "level": "warning", "field": "document.billing_amount",
                        "message": "요금 항목 합계 또는 청구금액 값이 없어(혹은 일부 금액 미입력) 검산을 건너뜀"})
    elif abs(all_sum - billing_amount) > 1:
        issues.append({"code": "CALC_MISMATCH", "level": "error", "field": "document.billing_amount",
                        "message": f"전체 요금 항목 합계({all_sum:,.0f}원)가 청구금액({billing_amount:,.0f}원)과 다름"})

    return issues


# ---------- 화면 (단계별로 한 화면씩만 보여줌) ----------

st.set_page_config(page_title="SCOPION 추출 테스트", page_icon="🌿", layout="wide")
st.title("SCOPION - 고지서 추출 테스트 (v2)")
st.caption("업로드 → AI 추출 → 값 확인/수정/승인 → 배출량 산정, 단계별 화면으로 진행됩니다. (공통스키마 v2.2 기준)")

STEP_ORDER = ["upload", "review", "calculate", "submission"]
STEP_LABELS = {"upload": "1. 업로드·추출", "review": "2. 확인·승인", "calculate": "3. 배출량 산정",
               "submission": "4. 원청 제출 양식"}

if "step" not in st.session_state:
    st.session_state["step"] = "upload"


def go_to(step):
    st.session_state["step"] = step
    st.rerun()


def reset_all():
    for k in ("record", "doc_type", "confirmed_record", "step"):
        st.session_state.pop(k, None)
    st.session_state["step"] = "upload"
    st.rerun()


current_step = st.session_state["step"]
current_idx = STEP_ORDER.index(current_step)
st.progress(current_idx / (len(STEP_ORDER) - 1))
st.caption(" → ".join(
    f"**[{label}]**" if key == current_step else label
    for key, label in STEP_LABELS.items()
))
st.divider()

# ===== 1단계: 업로드 & 추출 =====
if current_step == "upload":
    doc_type = st.radio("고지서 종류", ["electricity", "gas"], horizontal=True,
                         format_func=lambda x: "전기요금 고지서" if x == "electricity" else "도시가스 고지서")

    model = st.text_input("모델 ID", value="claude-sonnet-5-5")

    uploaded = st.file_uploader("고지서 이미지를 올려주세요", type=["jpg", "jpeg", "png"])

    if uploaded is not None:
        st.image(uploaded, caption="업로드한 이미지", width=320)

    if st.button("추출하기", type="primary", disabled=uploaded is None):
        api_key = get_api_key()
        if not api_key:
            api_err = st.session_state.get("_api_key_error")
            if api_err:
                st.error(f"API 키를 불러오지 못했습니다 — {api_err}")
            else:
                st.error("API 키를 찾지 못했습니다. (Streamlit Cloud: Settings → Secrets에 ANTHROPIC_API_KEY 등록 / "
                          "로컬: 이 폴더 또는 상위 폴더에 anthropic_key.txt 필요)")
        else:
            schema_path = os.path.join(HERE, f"llm_schema_{doc_type}_v2.json")
            if not os.path.exists(schema_path):
                st.error(f"{schema_path} 파일이 없습니다.")
            else:
                with st.spinner("Claude API에 요청 중..."):
                    try:
                        schema = json.load(open(schema_path, encoding="utf-8"))
                        system_prompt = build_system_prompt(doc_type)
                        img_b64 = encode_image(uploaded)
                        res = call_claude(model, schema, system_prompt, img_b64, api_key)
                        extracted = next((c["input"] for c in res["content"] if c["type"] == "tool_use"), None)
                    except Exception as ex:
                        st.error(f"추출 실패: {ex}")
                        extracted = None

                if extracted is not None:
                    record = build_v2_record(doc_type, extracted, model)
                    record["field_status"] = compute_field_status(record, doc_type)
                    record["validation"]["issues"] = run_validation(record)
                    st.session_state["record"] = record
                    st.session_state["doc_type"] = doc_type
                    go_to("review")

# ===== 2단계: 확인/수정 & 승인 =====
elif current_step == "review":
    record = st.session_state["record"]
    doc_type = st.session_state.get("doc_type")
    field_status = record.get("field_status", {})

    st.subheader("추출 결과 확인/수정")

    missing = [k for k, v in field_status.items() if v == "missing"]
    if missing:
        st.warning("다음 필수 항목이 비어 있습니다 — 아래에서 직접 입력해 주세요: " + ", ".join(missing))

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**문서 공통 정보 (document)**")
        for key, label in DOCUMENT_FIELDS:
            path = f"document.{key}"
            is_missing = field_status.get(path) == "missing"
            val = record["document"].get(key)
            new_val = st.text_input(
                label=f"⚠️ {label}" if is_missing else label,
                value="" if val is None else str(val),
                key=f"document_{key}",
            )
            record["document"][key] = new_val if new_val != "" else None

        st.markdown("**상세 항목 (detail)**")
        for key, label in DETAIL_FIELDS.get(doc_type, []):
            path = f"detail.{key}"
            is_missing = field_status.get(path) == "missing"
            val = record["detail"].get(key)
            new_val = st.text_input(
                label=f"⚠️ {label}" if is_missing else label,
                value="" if val is None else str(val),
                key=f"detail_{key}",
            )
            record["detail"][key] = new_val if new_val != "" else None

    with col2:
        st.markdown("**사용량 (activities)**")
        activities = record.get("activities") or [{}]
        act0 = activities[0]
        for key, label in ACTIVITY_FIELDS:
            path = f"activities[0].{key}"
            is_missing = field_status.get(path) == "missing"
            val = act0.get(key)
            new_val = st.text_input(
                label=f"⚠️ {label}" if is_missing else label,
                value="" if val is None else str(val),
                key=f"activity_{key}",
            )
            act0[key] = new_val if new_val != "" else None
        activities[0] = act0
        record["activities"] = activities

        st.markdown("**요금 항목 (charges)** — 표에서 직접 추가/삭제/수정할 수 있습니다")
        charges_input = [
            {"code": c.get("code"), "name_raw": c.get("name_raw"), "amount": c.get("amount")}
            for c in (record.get("charges") or [])
        ]
        edited_charges = st.data_editor(
            charges_input,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "code": st.column_config.SelectboxColumn("code", options=CHARGE_CODE_OPTIONS, required=True),
                "name_raw": st.column_config.TextColumn("고지서 표기명"),
                "amount": st.column_config.NumberColumn("금액(원)"),
            },
            key="charges_editor",
        )
        new_charges = []
        for c in edited_charges:
            code = c.get("code") or "other"
            new_charges.append({
                "code": code, "name_raw": c.get("name_raw"),
                "amount": c.get("amount"), "group": CHARGE_CODE_GROUP.get(code, "other"),
            })
        record["charges"] = new_charges
        if new_charges:
            st.caption("group (자동 계산): " + ", ".join(
                f"{CHARGE_CODE_LABEL.get(c['code'], c['code'])}→{c['group']}" for c in new_charges))

    # 수정 내용 반영해서 재검증
    record["field_status"] = compute_field_status(record, doc_type)
    record["validation"]["issues"] = run_validation(record)
    st.session_state["record"] = record

    st.subheader("검증 결과")
    issues = record["validation"]["issues"]
    has_error = any(i["level"] == "error" for i in issues)
    if not issues:
        st.success("검증 통과: 필수 항목이 모두 채워졌고, 요금 합계도 공급가액/청구금액과 일치합니다.")
    for issue in issues:
        if issue["level"] == "error":
            st.error(f"[{issue['code']}] {issue['message']}")
        else:
            st.warning(f"[{issue['code']}] {issue['message']}")
    if missing:
        has_error = True  # 필수값 비어있으면 승인 불가

    st.caption("규칙을 통과해도 자동으로 승인되지 않습니다. 관리자가 직접 확인 후 승인해야 합니다.")

    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("⬅ 처음부터 다시"):
            reset_all()
    with nav2:
        if st.button("승인 →", type="primary", disabled=has_error, use_container_width=True):
            record["status"] = "APPROVED"
            record["review"] = {"status": "approved", "approved_by": None,
                                 "approved_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
            record["history"].append({"action": "approved",
                                       "at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
            st.session_state["record"] = record
            st.session_state["confirmed_record"] = record
            go_to("calculate")
    if has_error:
        st.caption("⚠️ 필수 항목 누락 또는 검증 오류(error)가 남아있어 승인 버튼이 막혀 있습니다.")

# ===== 3단계: 배출량 산정 결과 대시보드 =====
elif current_step == "calculate":
    confirmed = st.session_state["confirmed_record"]

    with st.expander("승인된 레코드 (공통스키마 v2.2 모양) 펼쳐보기"):
        st.json(confirmed)

    doc_type = "electricity" if confirmed.get("document_type") == "electricity_bill" else "gas"
    doc_label = "전기 고지서" if doc_type == "electricity" else "도시가스 고지서"
    billing_month = (confirmed.get("document") or {}).get("billing_month")
    site = ((confirmed.get("document") or {}).get("site_address") or "").strip() or "미지정"
    year_val = None
    if billing_month and "-" in str(billing_month):
        try:
            year_val = int(str(billing_month).split("-")[0])
        except ValueError:
            year_val = None

    factors = load_factors()
    calc = compute_emission(confirmed, factors)
    act0 = (confirmed.get("activities") or [{}])[0]
    detail = confirmed.get("detail") or {}
    elec_kwh = _num(act0.get("value")) if doc_type == "electricity" else None
    heat_mj = _num(detail.get("total_heat_value")) if doc_type == "gas" else None
    gas_gj = heat_mj / 1000 if heat_mj is not None else None

    already_reflected = confirmed.get("status") == "CALCULATED"

    title_col, nav_col = st.columns([3, 1])
    with title_col:
        st.subheader(f"배출량 ({billing_month or '승인된 문서'} 기준 집계)")
    with nav_col:
        if st.button("원청 제출 양식 보기 →", use_container_width=True):
            go_to("submission")

    # ---- 반영된 데이터 누적 집계 (사업장·월별 승인분이 쌓이는 기록) ----
    history = load_history()
    records = history["records"]
    total_scope1 = sum(r["result_tco2e"] for r in records if r.get("scope") == 1)
    total_scope2 = sum(r["result_tco2e"] for r in records if r.get("scope") == 2)

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("반영분 중 배출합계", f"{total_scope1 + total_scope2:.1f} tCO2eq", help="아래 '반영하기'를 누른 건만 집계에 포함됩니다")
    s2.metric("Scope 1 직접배출", f"{total_scope1:.1f} tCO2eq", help="도시가스")
    s3.metric("Scope 2 간접배출", f"{total_scope2:.1f} tCO2eq", help="전력")
    s4.metric("반영 현황", f"{len(records)}건 반영됨")

    # ---- 아직 반영되지 않은 승인분 ----
    if calc["ok"] and not already_reflected:
        st.warning(f"⚠️ 아직 결과에 반영되지 않은 승인분이 있습니다 — {billing_month or '—'} {doc_label} ({site})")
        if st.button("반영하기", type="primary"):
            confirmed["emissions"] = {
                "formula": calc["formula"],
                "emission_factor": calc["emission_factor"],
                "input_snapshot": calc["input_snapshot"],
                "result_tco2e": calc["result_tco2e"],
                "calculated_at": calc["calculated_at"],
            }
            confirmed["status"] = "CALCULATED"
            st.session_state["confirmed_record"] = confirmed
            append_history_record(confirmed, calc, doc_type, site)
            st.success("반영했습니다. 아래 집계·그래프에 포함됩니다.")
            st.rerun()
    elif not calc["ok"]:
        st.info(f"ℹ️ 산정 보류: {calc['reason']} (배출계수는 관리자가 별도로 emission_factors.json에 등록합니다)")

    st.divider()

    # ---- Scope별 적용 계수 (읽기 전용 — 수정은 관리자 영역) ----
    col_g, col_e = st.columns(2)
    elec_factor = get_factor(factors, "electricity", year_val)
    gas_factor = get_factor(factors, "gas", year_val)

    with col_g:
        st.markdown("**Scope 1 · 도시가스**")
        if gas_factor:
            st.caption(f"계수 {gas_factor['value']} {gas_factor['unit']}")
            st.caption(f"출처: {gas_factor['source']}")
        else:
            st.caption(f"{year_val or '해당 연도'} 계수가 등록되어 있지 않습니다")
    with col_e:
        st.markdown("**Scope 2 · 전력**")
        if elec_factor:
            st.caption(f"계수 {elec_factor['value']} {elec_factor['unit']}")
            st.caption(f"출처: {elec_factor['source']}")
        else:
            st.caption(f"{year_val or '해당 연도'} 계수가 등록되어 있지 않습니다")
    st.caption("배출계수는 관리자만 emission_factors.json에서 등록·수정합니다 (이 화면에서는 수정할 수 없음).")

    st.divider()

    # ---- 사업장별 승인분 ----
    st.subheader("사업장별 승인분")
    if not records:
        st.caption("아직 반영된 승인분이 없습니다.")
    else:
        by_site = defaultdict(lambda: {1: 0.0, 2: 0.0})
        for r in records:
            by_site[r.get("site", "미지정")][r.get("scope", 2)] += r.get("result_tco2e") or 0.0
        site_rows = [
            {"사업장명": site_name, "Scope 1": f"{vals[1]:.1f}", "Scope 2": f"{vals[2]:.1f}",
             "합계": f"{vals[1] + vals[2]:.1f}"}
            for site_name, vals in sorted(by_site.items())
        ]
        st.dataframe(pd.DataFrame(site_rows), use_container_width=True, hide_index=True)
        st.caption("단위: tCO2eq. 표시값은 소수점 첫째 자리로 반올림했으며, 합계는 반올림 전 원본으로 계산됩니다.")

    st.divider()

    # ---- 월별 배출량 추이 ----
    st.subheader("월별 배출량 추이")
    monthly = defaultdict(lambda: {1: 0.0, 2: 0.0})
    for r in records:
        m = r.get("billing_month")
        if m:
            monthly[m][r.get("scope", 2)] += r.get("result_tco2e") or 0.0

    months = sorted(monthly.keys())
    pending_new_month = billing_month and billing_month not in monthly and calc["ok"] and not already_reflected
    if pending_new_month:
        months = sorted(months + [billing_month])

    if not months:
        st.caption("아직 반영된 월별 데이터가 없습니다.")
    else:
        scope1_vals = [monthly.get(m, {1: 0.0, 2: 0.0})[1] for m in months]
        scope2_vals = [monthly.get(m, {1: 0.0, 2: 0.0})[2] for m in months]
        totals = [a + b for a, b in zip(scope1_vals, scope2_vals)]
        max_total = max(totals) if any(totals) else 1.0

        def month_label(m):
            try:
                return f"{int(str(m).split('-')[1])}월"
            except (IndexError, ValueError):
                return str(m)

        if HAS_MATPLOTLIB:
            fig, ax = plt.subplots(figsize=(8, 3.2))
            x = list(range(len(months)))
            bar_width = 0.5
            ax.bar(x, scope1_vals, width=bar_width, label="Scope 1", color="#8fc9b8")
            ax.bar(x, scope2_vals, width=bar_width, bottom=scope1_vals, label="Scope 2", color="#1f7a5c")
            for i, m in enumerate(months):
                if pending_new_month and m == billing_month:
                    ax.bar([i], [max_total * 0.08 or 0.5], width=bar_width, color="none", edgecolor="#999999",
                           linestyle="--", linewidth=1.2)
                    ax.text(i, max_total * 0.1, "승인 전", ha="center", va="bottom", fontsize=9, color="#999999")
                elif totals[i] > 0:
                    ax.text(i, totals[i] + max_total * 0.03, f"{totals[i]:.1f}", ha="center", va="bottom", fontsize=9)
            ax.set_xticks(x)
            ax.set_xticklabels([month_label(m) for m in months])
            ax.set_xlim(-0.8, len(months) - 0.2)
            ax.set_ylim(0, max_total * 1.3 if max_total else 1)
            ax.set_ylabel("tCO2eq")
            ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.15), ncol=2, frameon=False)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            fig.tight_layout()
            st.pyplot(fig, bbox_inches="tight")
        else:
            chart_df = pd.DataFrame(
                {"Scope 1": scope1_vals, "Scope 2": scope2_vals},
                index=[month_label(m) for m in months],
            )
            st.bar_chart(chart_df, use_container_width=True)
            st.caption("더 보기 좋은 그래프(값 라벨, 승인 전 달 점선 표시)를 보려면 터미널에서 `pip install matplotlib` 실행 후 새로고침하세요.")
        st.caption("그래프는 '반영하기'를 누른 승인분만 포함합니다.")

    st.divider()
    if st.button("⬅ 새 고지서 추출하기 (처음부터)"):
        reset_all()

# ===== 4단계: 원청 제출 양식 =====
elif current_step == "submission":
    confirmed = st.session_state["confirmed_record"]

    st.info("ℹ️ 가상 원청 제출 양식입니다. 실제 원청(현대·기아 등) 서식은 아직 확보 전이라, 공개된 요구항목을 바탕으로 만든 시연용 양식입니다. 실제 서식을 받으면 항목 매핑만 교체합니다.")

    doc_type = "electricity" if confirmed.get("document_type") == "electricity_bill" else "gas"
    doc_label = "전기 고지서" if doc_type == "electricity" else "도시가스 고지서"
    billing_month = (confirmed.get("document") or {}).get("billing_month")
    year_val = None
    if billing_month and "-" in str(billing_month):
        try:
            year_val = int(str(billing_month).split("-")[0])
        except ValueError:
            year_val = None

    factors = load_factors()
    calc = compute_emission(confirmed, factors)
    act0 = (confirmed.get("activities") or [{}])[0]
    detail = confirmed.get("detail") or {}
    elec_kwh = _num(act0.get("value")) if doc_type == "electricity" else None
    heat_mj = _num(detail.get("total_heat_value")) if doc_type == "gas" else None
    gas_gj = heat_mj / 1000 if heat_mj is not None else None
    scope1 = calc["result_tco2e"] if (calc["ok"] and doc_type == "gas") else None
    scope2 = calc["result_tco2e"] if (calc["ok"] and doc_type == "electricity") else None

    st.subheader("원청 제출 양식 (자동 채움)")
    cola, colb = st.columns(2)
    with cola:
        company_name = st.text_input("업체명", key="org_company_name", placeholder="직접 입력")
    with colb:
        report_boundary = st.text_input("보고 경계", key="org_report_boundary", placeholder="예: 화성공장 1개 사업장")

    elec_factor = get_factor(factors, "electricity", year_val)
    gas_factor = get_factor(factors, "gas", year_val)

    submission_rows = [
        {"섹션": "A. 보고 경계·기간", "항목": "업체명", "값": company_name or "—",
         "단위": "", "출처": "직접 입력", "상태": "자동" if company_name else "입력 필요"},
        {"섹션": "", "항목": "보고 경계", "값": report_boundary or "—",
         "단위": "", "출처": "직접 입력", "상태": "자동" if report_boundary else "입력 필요"},
        {"섹션": "", "항목": "보고 기간", "값": billing_month or "—",
         "단위": "", "출처": "승인 문서의 청구월", "상태": "자동" if billing_month else "입력 필요"},
        {"섹션": "B. 에너지 소비", "항목": "전력 사용량", "값": f"{elec_kwh:,.0f}" if elec_kwh is not None else "—",
         "단위": "kWh", "출처": "승인된 전기 고지서", "상태": "자동" if elec_kwh is not None else "해당 없음"},
        {"섹션": "", "항목": "도시가스 사용열량", "값": f"{gas_gj:,.3f}" if gas_gj is not None else "—",
         "단위": "GJ", "출처": "승인된 가스 고지서 (MJ→GJ)", "상태": "자동" if gas_gj is not None else "해당 없음"},
        {"섹션": "C. Scope 1 (직접배출)", "항목": "도시가스 연소 배출량", "값": f"{scope1:.3f}" if scope1 is not None else "—",
         "단위": "tCO2eq", "출처": "사용열량 × 배출계수 (규칙 코드)",
         "상태": "자동" if scope1 is not None else ("해당 없음" if doc_type == "electricity" else "계수 필요")},
        {"섹션": "D. Scope 2 (간접배출)", "항목": "구매전력 배출량 (위치기반)", "값": f"{scope2:.3f}" if scope2 is not None else "—",
         "단위": "tCO2eq", "출처": "사용량 × 배출계수 (규칙 코드)",
         "상태": "자동" if scope2 is not None else ("해당 없음" if doc_type == "gas" else "계수 필요")},
        {"섹션": "E. 산정 방법론", "항목": "산정 방식", "값": "활동자료 × 배출계수",
         "단위": "", "출처": "규칙 코드 (LLM 미사용)", "상태": "자동"},
        {"섹션": "", "항목": f"가스 배출계수 {year_val or ''}".strip(),
         "값": gas_factor["value"] if gas_factor else "미등록", "단위": "kgCO2/GJ",
         "출처": gas_factor["source"] if gas_factor else "배출계수표", "상태": "자동" if gas_factor else "입력 필요"},
        {"섹션": "", "항목": f"전력 배출계수 {year_val or ''}".strip(),
         "값": elec_factor["value"] if elec_factor else "미등록", "단위": "tCO2eq/MWh",
         "출처": elec_factor["source"] if elec_factor else "배출계수표", "상태": "자동" if elec_factor else "입력 필요"},
        {"섹션": "F. 증빙·품질", "항목": "증빙 문서", "값": doc_label,
         "단위": "", "출처": "승인된 원본 고지서 목록", "상태": "자동"},
        {"섹션": "", "항목": "승인 이력", "값": "1건 관리자 승인",
         "단위": "", "출처": "검증·승인 로그", "상태": "자동"},
        {"섹션": "", "항목": "제3자 검증 여부", "값": "—",
         "단위": "", "출처": "현재 데이터로 알 수 없음", "상태": "추가 정보 필요"},
    ]
    st.dataframe(pd.DataFrame(submission_rows), use_container_width=True, hide_index=True)
    st.caption("같은 데이터를 서식마다 다시 입력하는 이중 수작업을 줄이는 것이 목표입니다.")

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        csv_df = pd.DataFrame(submission_rows)
        st.download_button(
            "양식 CSV 내려받기",
            data=csv_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="scopion_원청제출양식.csv",
            mime="text/csv",
        )
    with dcol2:
        st.download_button(
            "공통스키마 JSON 내려받기",
            data=json.dumps(confirmed, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="scopion_record.json",
            mime="application/json",
        )

    st.divider()
    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("⬅ 배출량 산정으로 돌아가기"):
            go_to("calculate")
    with nav2:
        if st.button("⬅ 새 고지서 추출하기 (처음부터)", use_container_width=True):
            reset_all()
