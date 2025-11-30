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
st.title("🤖 만능 AI 에이전트 (파일 업로드 기능 추가됨)")

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
    # Assistant 생성 (파일은 메시지 레벨에서 첨부하므로 여기선 기본 설정만)
    assistant = client.beta.assistants.create(
        name="Streamlit File Assistant",
        instructions="당신은 데이터 전문가입니다. 업로드된 파일이 있다면 code_interpreter나 file_search를 사용해 내용을 분석하세요.",
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
# 4. 사이드바: 파일 업로드 UI [새로 추가된 부분 ⭐]
# ---------------------------------------------------------
with st.sidebar:
    st.header("📂 파일 업로드")
    uploaded_file = st.file_uploader("AI에게 분석시킬 파일을 올리세요", type=["txt", "csv", "xlsx", "pdf", "png", "jpg"])
    
    st.info("💡 파일을 올린 후 채팅창에 '이 파일 분석해줘'라고 입력하세요.")

# ---------------------------------------------------------
# 5. 채팅 인터페이스
# ---------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
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

    # 2. 파일 처리 로직 [새로 추가된 부분 ⭐]
    msg_params = {"thread_id": st.session_state.thread.id, "role": "user", "content": prompt}
    
    # 사용자가 파일을 업로드 했다면 Azure에 올리고 메시지에 첨부
    if uploaded_file:
        with st.spinner("파일을 Azure OpenAI에 업로드 중..."):
            # Streamlit의 파일 객체를 Azure가 좋아하는 형태로 업로드
            # uploaded_file은 BytesIO 형태이므로 바로 전달 가능
            file_response = client.files.create(
                file=uploaded_file,
                purpose="assistants"
            )
            
            # 메시지에 첨부 (Code Interpreter와 File Search 모두 사용 가능하게 설정)
            msg_params["attachments"] = [
                {
                    "file_id": file_response.id, 
                    "tools": [{"type": "code_interpreter"}, {"type": "file_search"}]
                }
            ]
            st.toast(f"파일이 첨부되었습니다: {uploaded_file.name}")

    # 3. 메시지 전송
    client.beta.threads.messages.create(**msg_params)

    # 4. 실행 및 폴링
    with st.chat_message("assistant"):
        status_box = st.status("AI가 생각 중입니다...", expanded=True)
        
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
