import streamlit as st
import os
import time
import json
import requests
from datetime import datetime, timedelta, timezone
from openai import AzureOpenAI
from dotenv import load_dotenv

# 1. 환경 설정
load_dotenv()

st.set_page_config(page_title="만능 AI 에이전트", layout="wide")
st.title("🤖 만능 AI 에이전트 (멀티모달 지원)")

# API 키 가져오기
api_key = st.secrets.get("AZURE_OAI_KEY", os.getenv("AZURE_OAI_KEY"))
endpoint = st.secrets.get("AZURE_OAI_ENDPOINT", os.getenv("AZURE_OAI_ENDPOINT"))
weather_key = st.secrets.get("OPENWEATHER_API_KEY", os.getenv("OPENWEATHER_API_KEY"))

if not api_key or not endpoint:
    st.error("API 키가 설정되지 않았습니다.")
    st.stop()

client = AzureOpenAI(
    api_key=api_key,
    api_version="2024-05-01-preview",
    azure_endpoint=endpoint
)

# ---------------------------------------------------------
# 2. 도구 함수 정의
# ---------------------------------------------------------
def get_location_data(location):
    if not weather_key: return None
    url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={weather_key}&units=metric"
    try:
        response = requests.get(url)
        return response.json() if response.status_code == 200 else None
    except: return None

def get_current_weather(location):
    data = get_location_data(location)
    if data:
        return json.dumps({
            "location": location, "temperature": round(data["main"]["temp"], 1),
            "unit": "celsius", "description": data["weather"][0]["description"]
        })
    return json.dumps({"error": "City not found"})

def get_current_time(location):
    data = get_location_data(location)
    if data:
        local_time = datetime.now(timezone.utc) + timedelta(seconds=data["timezone"])
        return json.dumps({"location": location, "current_time": local_time.strftime("%Y-%m-%d %I:%M %p")})
    return json.dumps({"error": "City not found"})

# ---------------------------------------------------------
# 3. Assistant & Thread 초기화
# ---------------------------------------------------------
@st.cache_resource
def create_assistant():
    assistant = client.beta.assistants.create(
        name="Streamlit Multi-Modal Bot",
        instructions="당신은 데이터 전문가이자 비전 능력을 가진 AI입니다. 이미지가 주어지면 내용을 설명하고, 데이터 파일이 주어지면 분석하세요.",
        model="gpt-4o-mini", 
        tools=[
            {"type": "code_interpreter"}, 
            {"type": "file_search"},
            {"type": "function", "function": {"name": "get_current_weather", "description": "Get current weather.", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}},
            {"type": "function", "function": {"name": "get_current_time", "description": "Get current local time.", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}
        ],
    )
    return assistant

if "assistant" not in st.session_state:
    st.session_state.assistant = create_assistant()
    st.session_state.thread = client.beta.threads.create()
    st.session_state.messages = [] 

# ---------------------------------------------------------
# 4. 사이드바: 파일 업로드 UI
# ---------------------------------------------------------
with st.sidebar:
    st.header("📂 파일 업로드")
    uploaded_file = st.file_uploader("이미지나 문서를 올리세요", type=["txt", "csv", "xlsx", "pdf", "png", "jpg", "jpeg", "gif"])
    st.info("💡 파일을 올린 후 채팅창에 질문을 입력하세요.")

# ---------------------------------------------------------
# 5. 채팅 인터페이스
# ---------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        # 텍스트가 리스트(멀티모달)일 수도 있고 문자열일 수도 있음
        if isinstance(msg["content"], list):
            for content_part in msg["content"]:
                if content_part["type"] == "text":
                    st.markdown(content_part["text"])
        else:
            st.markdown(msg["content"])
            
        if "images" in msg:
            for img_data in msg["images"]:
                st.image(img_data)
        if "files" in msg:
            for f_name, f_data in msg["files"]:
                st.download_button(label=f"📂 {f_name} 다운로드", data=f_data, file_name=f_name)

if prompt := st.chat_input("메시지를 입력하세요..."):
    # 1. 사용자 메시지 표시
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 2. 파일 처리 로직 (이미지 vs 문서 분기 처리)
    msg_content = prompt
    msg_attachments = []

    if uploaded_file:
        with st.spinner("파일 업로드 및 처리 중..."):
            file_response = client.files.create(
                file=uploaded_file,
                purpose="assistants"
            )
            file_id = file_response.id
            file_ext = os.path.splitext(uploaded_file.name)[1].lower()

            # [이미지 파일] -> Vision (Content에 포함)
            if file_ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                msg_content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_file", "image_file": {"file_id": file_id}}
                ]
                st.toast(f"🖼️ 이미지 분석 모드: {uploaded_file.name}")
            
            # [문서 파일] -> Tools (Attachments에 포함)
            else:
                msg_attachments = [
                    {
                        "file_id": file_id, 
                        "tools": [{"type": "code_interpreter"}, {"type": "file_search"}]
                    }
                ]
                st.toast(f"📄 문서 분석 모드: {uploaded_file.name}")

    # 3. 메시지 전송
    client.beta.threads.messages.create(
        thread_id=st.session_state.thread.id,
        role="user",
        content=msg_content,
        attachments=msg_attachments
    )

    # 4. 실행 및 폴링
    with st.chat_message("assistant"):
        status_box = st.status("AI가 처리 중입니다...", expanded=True)
        
        run = client.beta.threads.runs.create(thread_id=st.session_state.thread.id, assistant_id=st.session_state.assistant.id)
        
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=st.session_state.thread.id, run_id=run.id)
            
            if run_status.status == 'completed':
                break
            elif run_status.status == 'requires_action':
                tool_calls = run_status.required_action.submit_tool_outputs.tool_calls
                tool_outputs = []
                for tool in tool_calls:
                    func_name = tool.function.name
                    args = json.loads(tool.function.arguments)
                    
                    if func_name == "get_current_weather":
                        output = get_current_weather(args["location"])
                        status_box.write(f" -> 날씨 조회: {args['location']}")
                    elif func_name == "get_current_time":
                        output = get_current_time(args["location"])
                        status_box.write(f" -> 시간 조회: {args['location']}")
                    else: output = "{}"
                    tool_outputs.append({"tool_call_id": tool.id, "output": output})
                
                client.beta.threads.runs.submit_tool_outputs(thread_id=st.session_state.thread.id, run_id=run.id, tool_outputs=tool_outputs)
            elif run_status.status in ['failed', 'cancelled', 'expired']:
                st.error("오류 발생")
                break
            time.sleep(1)
        
        status_box.update(label="답변 완료!", state="complete", expanded=False)

        # 5. 결과 처리
        messages = client.beta.threads.messages.list(thread_id=st.session_state.thread.id)
        latest_msg = messages.data[0]
        
        response_txt = ""
        images_to_show = []
        files_to_download = []

        for content in latest_msg.content:
            if content.type == 'text':
                response_txt += content.text.value
                if content.text.annotations:
                    for annotation in content.text.annotations:
                        if annotation.type == 'file_path':
                            file_id = annotation.file_path.file_id
                            file_name = os.path.basename(annotation.text)
                            file_data = client.files.content(file_id).read()
                            files_to_download.append((file_name, file_data))
            elif content.type == 'image_file':
                file_id = content.image_file.file_id
                image_data = client.files.content(file_id).read()
                images_to_show.append(image_data)

        st.markdown(response_txt)
        for img_data in images_to_show: st.image(img_data)
        for f_name, f_data in files_to_download:
            st.download_button(label=f"📂 {f_name} 다운로드", data=f_data, file_name=f_name)

        st.session_state.messages.append({
            "role": "assistant", 
            "content": response_txt,
            "images": images_to_show,
            "files": files_to_download
        })
