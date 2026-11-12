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
    # Streamlit Cloud에 올렸을 때는 "Secrets"에 저장한 값을 쓴다 (코드/저장소에 키를 넣지 않기 위함).
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
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


# ---------- 화면 ----------

st.set_page_config(page_title="SCOPION 추출 테스트", page_icon="🌿", layout="wide")
st.title("SCOPION - 고지서 추출 테스트 (v2)")
st.caption("업로드 → AI 추출 → 값 확인/수정 → 규칙기반 검증 → 승인까지의 데모입니다. (공통스키마 v2.2 기준)")

doc_type = st.radio("고지서 종류", ["electricity", "gas"], horizontal=True,
                     format_func=lambda x: "전기요금 고지서" if x == "electricity" else "도시가스 고지서")

model = st.text_input("모델 ID", value="claude-sonnet-5-5")

uploaded = st.file_uploader("고지서 이미지를 올려주세요", type=["jpg", "jpeg", "png"])

if uploaded is not None:
    st.image(uploaded, caption="업로드한 이미지", width=320)

if st.button("추출하기", type="primary", disabled=uploaded is None):
    api_key = get_api_key()
    if not api_key:
        st.error("anthropic_key.txt를 이 폴더(또는 상위 폴더)에서 찾지 못했습니다.")
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

if "record" in st.session_state:
    record = st.session_state["record"]
    doc_type = st.session_state.get("doc_type", doc_type)
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
    if st.button("승인", type="primary", disabled=has_error):
        record["status"] = "APPROVED"
        record["review"] = {"status": "approved", "approved_by": None,
                             "approved_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
        record["history"].append({"action": "approved",
                                   "at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
        st.session_state["record"] = record
        st.session_state["confirmed_record"] = record
        st.success("승인되었습니다. (다음 단계: 저장·합산 → 배출량 산정으로 연결)")
    if has_error:
        st.caption("⚠️ 필수 항목 누락 또는 검증 오류(error)가 남아있어 승인 버튼이 막혀 있습니다.")

if "confirmed_record" in st.session_state:
    st.subheader("승인된 레코드 (다음 단계로 넘어갈 데이터, 공통스키마 v2.2 모양)")
    st.json(st.session_state["confirmed_record"])

    st.divider()
    st.subheader("배출계수 (연도·연료별)")
    st.warning("⚠️ 아래 값은 데모용 예시이며 공식 배출계수가 아닙니다. 공식 출처가 확정되면 이 표의 값만 교체하면 됩니다.")

    factors = load_factors()
    current_fuel = "electricity" if st.session_state["confirmed_record"].get("document_type") == "electricity_bill" else "gas"
    fuel_label = "전력(전기)" if current_fuel == "electricity" else "도시가스"
    unit_label = "tCO2eq/MWh" if current_fuel == "electricity" else "kgCO2/GJ"

    st.caption(f"지금 승인된 문서는 **{fuel_label}**입니다. 아래 표에서 해당 연료의 연도별 계수를 확인·수정할 수 있습니다.")
    edited_factor_rows = st.data_editor(
        factors.get(current_fuel, []),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "year": st.column_config.NumberColumn("연도", format="%d"),
            "value": st.column_config.NumberColumn(f"배출계수 ({unit_label})"),
            "unit": st.column_config.TextColumn("단위"),
            "source": st.column_config.TextColumn("출처"),
        },
        key="factor_editor",
    )
    if st.button("배출계수 저장"):
        factors[current_fuel] = edited_factor_rows
        save_factors(factors)
        st.success("emission_factors.json에 저장했습니다.")
    factors[current_fuel] = edited_factor_rows  # 저장 전이라도 바로 아래 산정에 반영

    st.divider()
    st.subheader("배출량 산정 (사용량 × 배출계수)")
    calc = compute_emission(st.session_state["confirmed_record"], factors)
    if not calc["ok"]:
        st.info(f"산정 보류: {calc['reason']}")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("배출량", f"{calc['result_tco2e']:.3f} tCO2e")
        c2.metric("적용 배출계수", f"{calc['emission_factor']['value']} {calc['emission_factor']['unit']}")
        c3.metric("적용 연도", calc["emission_factor"]["year"])
        st.caption(f"출처: {calc['emission_factor']['source']}")
        st.code(calc["formula"], language=None)

        if st.button("이 산정 결과를 레코드에 반영"):
            st.session_state["confirmed_record"]["emissions"] = {
                "formula": calc["formula"],
                "emission_factor": calc["emission_factor"],
                "input_snapshot": calc["input_snapshot"],
                "result_tco2e": calc["result_tco2e"],
                "calculated_at": calc["calculated_at"],
            }
            st.session_state["confirmed_record"]["status"] = "CALCULATED"
            st.success("레코드의 emissions 항목에 산정 결과를 채워 넣었습니다. (위 JSON을 다시 펼쳐보면 반영돼 있어요)")
