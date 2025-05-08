import argparse
import os
import time
import requests
from threading import Thread
import gradio as gr
import json
import sseclient

class LLMChatHandler():
    def __init__(self, api_base_url: str):
        self.api_base_url = api_base_url
        print(f"VLLM API 서버 연결: {api_base_url}")
        
        # API 서버 연결 확인
        try:
            response = requests.get(f"{self.api_base_url}/ping")
            if response.status_code == 200:
                models = response.json()
                print(f"연결된 모델: {models}")
            else:
                print(f"API 서버 연결 실패: {response.status_code}")
        except Exception as e:
            print(f"API 서버 연결 오류: {str(e)}")

    def generate_response(self, message, history):
        """VLLM API를 호출하여 응답을 생성합니다."""
        # 스트리밍 시작 시간 기록
        start_time = time.time()
        
        # 메시지 형식 준비
        messages = []
        for msg in history:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                messages.append(msg)
        
        # 현재 메시지 추가
        messages.append({"role": "user", "content": message})
        
        # API 요청 준비
        api_url = f"{self.api_base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "model": "Qwen/Qwen2.5-14B-Instruct",  # VLLM 서버에 로드된 모델
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.8,
            "stream": True,  # 스트리밍 활성화
        }
        
        try:
            # SSE 스트리밍 요청
            response = requests.post(api_url, headers=headers, json=payload, stream=True)
            
            if response.status_code != 200:
                error_msg = f"API 오류: {response.status_code} - {response.text}"
                print(error_msg)
                yield error_msg
                return
            
            # 첫 토큰 시간 측정 변수
            is_first_token = True
            first_token_time = 0
            generated_text = ""
            token_count = 0
            
            # SSE 클라이언트로 이벤트 처리
            client = sseclient.SSEClient(response)
            
            for event in client.events():
                if event.data == "[DONE]":
                    break
                
                try:
                    data = json.loads(event.data)
                    if "choices" in data and len(data["choices"]) > 0:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        
                        if content:
                            # 첫 토큰 시간 기록
                            if is_first_token:
                                first_token_time = time.time()
                                is_first_token = False
                            
                            generated_text += content
                            token_count += 1
                            yield generated_text + " ⌛"
                except Exception as e:
                    print(f"이벤트 처리 오류: {str(e)}")
            
            # 생성 완료 시간 계산
            end_time = time.time()
            generation_time = end_time - start_time
            first_token_latency = first_token_time - start_time if not is_first_token else 0
            
            # 생성 속도 계산 (초당 토큰)
            if generation_time > 0 and token_count > 0:
                tokens_per_second = token_count / generation_time
            else:
                tokens_per_second = 0
            
            # 생성 통계 추가
            stats = f"\n\n---\n✅ 생성 완료 (토큰: {token_count}개, 시간: {generation_time:.2f}초, 속도: {tokens_per_second:.1f}토큰/초, 첫 토큰: {first_token_latency:.2f}초)"
            
            # 최종 텍스트 반환
            yield generated_text + stats
            
        except Exception as e:
            error_msg = f"API 호출 오류: {str(e)}"
            print(error_msg)
            yield error_msg

def main(args):
    print(f"VLLM 서버에 연결 중: {args.api_base_url}")
    hdlr = LLMChatHandler(api_base_url=args.api_base_url)

    with gr.Blocks(title=f"스터닝 박스", fill_height=True) as demo:
        gr.Markdown(
            f"<h2>📦스터닝 박스📦</h2>"
        )
        
        # 상태 표시 추가
        status = gr.Markdown("✨ 준비 완료", elem_id="status")
        
        # 메시지 형식 사용으로 경고 제거
        chatbot = gr.Chatbot(type='messages', scale=20, render_markdown=True)
        
        with gr.Row():
            msg = gr.Textbox(
                show_label=False,
                placeholder="메시지를 입력하세요...",
                container=False,
                scale=8
            )
            submit = gr.Button("전송", scale=1)
        
        clear = gr.Button("대화 지우기")
        
        def user_message(message, history, status_text=None):
            """사용자 메시지를 추가하는 함수"""
            if message.strip() == "":
                return "", history, "✨ 준비 완료"
            
            # 이미 history가 있으면 그대로 사용
            if history is None:
                history = []
            
            # 메시지 형식으로 추가
            history.append({"role": "user", "content": message})
            return "", history, "⌛ 응답 생성 중..."
        
        def bot_response(history, status_text=None):
            """봇 응답을 생성하는 함수"""
            if not history:
                yield history, "✨ 준비 완료"
                return
            
            # 마지막 사용자 메시지 가져오기
            last_user_message = history[-1]["content"]
            history_so_far = history[:-1]
            
            # 응답 생성
            for new_text in hdlr.generate_response(last_user_message, history_so_far):
                new_history = history.copy()
                
                # 스트리밍 중인지 완료된건지 확인
                if new_text.endswith("⌛"):
                    status_update = "⌛ 응답 생성 중..."
                    new_text = new_text[:-1]  # 진행 중 표시 제거
                elif new_text.endswith("초)"):
                    status_update = "✅ 응답 생성 완료"
                else:
                    status_update = "✨ 준비 완료"
                
                # 봇 응답 추가 (메시지 형식)
                if len(new_history) > 0 and new_history[-1]["role"] == "assistant":
                    new_history[-1]["content"] = new_text
                else:
                    new_history.append({"role": "assistant", "content": new_text})
                
                yield new_history, status_update
        
        # 이벤트 연결
        msg.submit(
            user_message, 
            [msg, chatbot, status], 
            [msg, chatbot, status],
            queue=False
        ).then(
            bot_response,
            [chatbot, status],
            [chatbot, status]
        )
        
        submit.click(
            user_message, 
            [msg, chatbot, status], 
            [msg, chatbot, status],
            queue=False
        ).then(
            bot_response,
            [chatbot, status],
            [chatbot, status]
        )
        
        clear.click(lambda: ([], "✨ 준비 완료"), None, [chatbot, status], queue=False)

    demo.queue().launch(server_name="0.0.0.0", server_port=args.port)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="OSS Chatbot",
        description="Run open source LLMs from HuggingFace with a simple chat interface")

    parser.add_argument("--api-base-url", default="http://localhost:7000", help="VLLM 서버 API URL")
    parser.add_argument("--port", default=8000, type=int, help="Gradio 앱 포트 번호")
    args = parser.parse_args()

    main(args) 