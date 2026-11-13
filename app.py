"""SCOPION - Streamlit 데모 앱 (Week1: 업로드 + AI 추출 + 간단 검증)
실행법: 터미널(cmd)에서 이 파일이 있는 폴더로 이동한 뒤
    streamlit run app.py
를 입력하면 브라우저가 자동으로 열립니다.
"""
import json
import os
import sys
import base64
import io
import urllib.request
import urllib.error

import streamlit as st
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------- 기존 run_llm_single.py와 동일한 로직 (재사용) ----------

def get_api_key():
    for p in [os.path.join(HERE, 'anthropic_key.txt'), os.path.join(os.path.dirname(HERE), 'anthropic_key.txt')]:
        if os.path.exists(p):
            return open(p, encoding='utf-8').read().strip()
    return None


def load_system_prompt():
    path = os.path.join(HERE, 'llm_prompt.md')
    text = open(path, encoding='utf-8').read()
    return text.split('## 시스템 프롬프트')[1].split('## 사용자 프롬프트')[0].strip()


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


# ---------- 아주 단순한 규칙 기반 검증 (Week1 "검증 로직"의 시작 버전) ----------

def check_fields(result: dict):
    """UNKNOWN이거나 비어 있는 값을 찾아서 경고 목록으로 돌려준다."""
    warnings = []
    for key, value in result.items():
        if value is None or str(value).strip() in ("", "UNKNOWN"):
            warnings.append(key)
    return warnings


# ---------- 화면 (여기서부터가 Streamlit 코드) ----------

st.set_page_config(page_title="SCOPION 추출 테스트", page_icon="🌿")
st.title("SCOPION - 고지서 추출 테스트")
st.caption("업로드 → AI 추출 → 값 확인/수정까지의 1단계 데모입니다.")

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
        schema_path = os.path.join(HERE, f"llm_schema_{doc_type}.json")
        if not os.path.exists(schema_path):
            st.error(f"{schema_path} 파일이 없습니다.")
        else:
            with st.spinner("Claude API에 요청 중..."):
                try:
                    schema = json.load(open(schema_path, encoding="utf-8"))
                    system_prompt = load_system_prompt()
                    img_b64 = encode_image(uploaded)
                    res = call_claude(model, schema, system_prompt, img_b64, api_key)
                    result = next((c["input"] for c in res["content"] if c["type"] == "tool_use"), None)
                except Exception as ex:
                    st.error(f"추출 실패: {ex}")
                    result = None

            if result is not None:
                st.session_state["last_result"] = result

if "last_result" in st.session_state:
    result = st.session_state["last_result"]
    st.subheader("추출 결과")

    warnings = check_fields(result)
    if warnings:
        st.warning("다음 항목은 값을 확인할 수 없었습니다(UNKNOWN) — 아래에서 직접 입력해 주세요: "
                    + ", ".join(warnings))
    else:
        st.success("모든 항목이 정상적으로 추출되었습니다.")

    st.write("항목별로 값을 확인하고, 필요하면 직접 고칠 수 있습니다.")
    edited = {}
    for key, value in result.items():
        is_warning = key in warnings
        edited[key] = st.text_input(
            label=f"⚠️ {key}" if is_warning else key,
            value="" if value == "UNKNOWN" else str(value),
            key=f"field_{key}",
        )

    if st.button("이 값으로 확정"):
        st.session_state["confirmed_result"] = edited
        st.success("확정되었습니다. (다음 단계: 배출량 산정으로 연결)")

if "confirmed_result" in st.session_state:
    st.subheader("확정된 값 (산정 단계로 넘어갈 데이터)")
    st.json(st.session_state["confirmed_result"])
