import streamlit as st
import os
import time
import json
import requests
import io
from datetime import datetime, timedelta, timezone
from openai import AzureOpenAI
from dotenv import load_dotenv

# 1. 환경 설정 및 클라이언트 초기화
load_dotenv()

st.set_page_config(page_title="만능 AI 에이전트", layout="wide")
st.title("🤖 만능 AI 에이전트 (날씨/지식/코딩/엑셀)")

# API 키 가져오기 (스트림릿 Cloud의 Secrets 또는 로컬 .env)
# Streamlit Cloud 배포 시 st.secrets를 우선 사용하도록 설정
api_key = st.secrets.get("AZURE_OAI_KEY", os.getenv("AZURE_OAI_KEY"))
endpoint = st.secrets.get("AZURE_OAI_ENDPOINT", os.getenv("AZURE_OAI_ENDPOINT"))
weather_key = st.secrets.get("OPENWEATHER_API_KEY", os.getenv("OPENWEATHER_API_KEY"))

if not api_key or not endpoint:
    st.error("API 키가 설정되지 않았습니다. .env 파일이나 Secrets를 확인해주세요.")
    st.stop()

client = AzureOpenAI(
    api_key=api_key,
    api_version="2024-05-01-preview",
    azure_endpoint=endpoint
)

# ---------------------------------------------------------
# 2. 도구 함수 정의 (캐싱 필요 없음, 단순 호출)
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
# 3. Assistant & Thread 초기화 (한 번만 실행되도록 설정)
# ---------------------------------------------------------
@st.cache_resource
def create_assistant_and_file():
    # 1. 가이드북 파일 생성 및 업로드
    filename = "seoul_weather_guide.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("""[서울 날씨 가이드] 서울의 겨울은 춥고 건조하며 패딩이 필수입니다. 여름은 덥고 습하며 장마철엔 우산이 필요합니다.""")
    
    file_object = client.files.create(file=open(filename, "rb"), purpose="assistants")
    
    # 2. Assistant 생성
    assistant = client.beta.assistants.create(
        name="Streamlit Super Bot",
        instructions="당신은 데이터 전문가입니다. 날씨/시간은 함수를, 가이드북은 파일검색을, 계산/파일생성/그래프는 코드 인터프리터를 사용하세요.",
        model="gpt-4o-mini",  # 배포명 확인 필요
        tools=[
            {"type": "code_interpreter"}, 
            {"type": "file_search"},
            {"type": "function", "function": {"name": "get_current_weather", "description": "Get current weather.", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}},
            {"type": "function", "function": {"name": "get_current_time", "description": "Get current local time.", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}
        ],
    )
    return assistant, file_object

# 세션 상태 초기화
if "assistant" not in st.session_state:
    with st.spinner("AI 에이전트를 준비 중입니다..."):
        st.session_state.assistant, st.session_state.file_obj = create_assistant_and_file()
        st.session_state.thread = client.beta.threads.create()
        st.session_state.messages = [] # 화면 표시용 메시지

# ---------------------------------------------------------
# 4. 채팅 인터페이스
# ---------------------------------------------------------

# 이전 대화 출력
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # 이미지나 파일이 있으면 표시
        if "images" in msg:
            for img_data in msg["images"]:
                st.image(img_data)
        if "files" in msg:
            for f_name, f_data in msg["files"]:
                st.download_button(label=f"📂 {f_name} 다운로드", data=f_data, file_name=f_name)

# 사용자 입력 처리
if prompt := st.chat_input("날씨, 시간, 그래프, 엑셀 파일 생성 등을 요청해보세요!"):
    # 1. 사용자 메시지 표시
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 2. Thread에 메시지 추가 (파일 첨부 로직 포함)
    msg_params = {"thread_id": st.session_state.thread.id, "role": "user", "content": prompt}
    
    # (옵션) 가이드북 파일을 항상 참조하고 싶다면 아래 주석 해제 (비용 절약을 위해 여기선 생략하거나 필요시 추가)
    # msg_params["attachments"] = [{"file_id": st.session_state.file_obj.id, "tools": [{"type": "file_search"}]}]
    
    client.beta.threads.messages.create(**msg_params)

    # 3. 실행 및 폴링 (상태 표시)
    with st.chat_message("assistant"):
        status_box = st.status("AI가 작업 중입니다...", expanded=True)
        
        run = client.beta.threads.runs.create(thread_id=st.session_state.thread.id, assistant_id=st.session_state.assistant.id)
        
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=st.session_state.thread.id, run_id=run.id)
            
            if run_status.status == 'completed':
                break
            elif run_status.status == 'requires_action':
                # 함수 호출 처리
                status_box.write("🛠️ 외부 도구(함수)를 호출하고 있습니다...")
                tool_calls = run_status.required_action.submit_tool_outputs.tool_calls
                tool_outputs = []
                for tool in tool_calls:
                    func_name = tool.function.name
                    args = json.loads(tool.function.arguments)
                    
                    if func_name == "get_current_weather":
                        output = get_current_weather(args["location"])
                        status_box.write(f" -> 날씨 조회 완료: {args['location']}")
                    elif func_name == "get_current_time":
                        output = get_current_time(args["location"])
                        status_box.write(f" -> 시간 조회 완료: {args['location']}")
                    else:
                        output = "{}"
                    
                    tool_outputs.append({"tool_call_id": tool.id, "output": output})
                
                client.beta.threads.runs.submit_tool_outputs(thread_id=st.session_state.thread.id, run_id=run.id, tool_outputs=tool_outputs)
            
            elif run_status.status in ['failed', 'cancelled', 'expired']:
                st.error("오류가 발생했습니다.")
                break
            time.sleep(1)
        
        status_box.update(label="작업 완료!", state="complete", expanded=False)

        # 4. 결과 처리 (텍스트, 이미지, 파일)
        messages = client.beta.threads.messages.list(thread_id=st.session_state.thread.id)
        latest_msg = messages.data[0]
        
        response_txt = ""
        images_to_show = []
        files_to_download = []

        for content in latest_msg.content:
            if content.type == 'text':
                response_txt += content.text.value
                # 주석(Annotation) 처리 - 파일 다운로드
                if content.text.annotations:
                    for annotation in content.text.annotations:
                        if annotation.type == 'file_path':
                            file_id = annotation.file_path.file_id
                            file_name = os.path.basename(annotation.text) # 샌드박스 경로 제거
                            
                            # 파일 데이터 메모리로 다운로드
                            file_data = client.files.content(file_id).read()
                            files_to_download.append((file_name, file_data))
                            
            elif content.type == 'image_file':
                # 이미지 다운로드
                file_id = content.image_file.file_id
                image_data = client.files.content(file_id).read()
                images_to_show.append(image_data)

        # 5. 결과 화면 출력 및 저장
        st.markdown(response_txt)
        
        for img_data in images_to_show:
            st.image(img_data)
            
        for f_name, f_data in files_to_download:
            st.download_button(label=f"📂 {f_name} 다운로드", data=f_data, file_name=f_name)

        # 세션에 저장 (새로고침 시 유지용)
        st.session_state.messages.append({
            "role": "assistant", 
            "content": response_txt,
            "images": images_to_show,
            "files": files_to_download
        })
