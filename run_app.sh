#!/bin/bash

# 스크립트 디렉토리를 기준으로 설정
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PROJECT_ROOT="$SCRIPT_DIR" # Poetry를 사용하지 않으므로 이 변수는 덜 중요해짐

echo "====== Phi-4 서버 구성 (독립 환경) ======"
echo "스크립트 디렉토리: $SCRIPT_DIR"
echo "현재 작업 디렉토리: $(pwd)"

# 필요한 패키지 설치
echo "필요한 Python 패키지를 설치합니다: vllm, requests, sseclient-py, gradio"
pip install vllm requests sseclient-py gradio

# 로그 파일 및 PID 파일 설정
LOG_FILE="/tmp/vllm_phi4_server.log"
PID_FILE="/tmp/vllm_phi4_server.pid"
MODEL_TO_SERVE="microsoft/phi-4" # 사용할 모델 ID

# 이전 로그 및 PID 파일 제거
rm -f "$LOG_FILE" "$PID_FILE"

# 백그라운드에서 VLLM 서버 실행
echo "1. VLLM (OpenAI API) 서버를 백그라운드에서 시작합니다..."
echo "   모델: $MODEL_TO_SERVE"
echo "   로그 파일: $LOG_FILE"
echo "   모델 로딩에는 몇 분 정도 소요될 수 있습니다. (14B 모델)"

# VLLM 서버 실행 (poetry run 제거)
python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_TO_SERVE" \
  --host 0.0.0.0 \
  --port 7000 \
  --dtype auto \
  --trust-remote-code \
  --max-model-len 8192 \
  --max-num-seqs 16 \
  --gpu-memory-utilization 0.90 > "$LOG_FILE" 2>&1 & # Phi-4 (14B)에 맞게 조절

# VLLM 서버 PID 저장
VLLM_PID=$!
echo $VLLM_PID > "$PID_FILE"

echo "VLLM 서버가 백그라운드로 시작되었습니다. (PID: $VLLM_PID)"
echo "서버가 준비될 때까지 기다립니다..."

# 서버가 준비될 때까지 대기 (헬스 체크)
MAX_RETRIES=180 # 15분 (180 * 5초), 14B 모델 로딩 시간 고려
RETRY_COUNT=0
SERVER_RESPONDING=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s "http://localhost:7000/health" | grep -q "true"; then
        echo "API 서버 응답 확인됨 (헬스 체크 성공)."
        SERVER_RESPONDING=true
        break
    fi
    
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo "API 서버 응답 대기 중... ($RETRY_COUNT/$MAX_RETRIES 시도, 5초 간격)"
    sleep 5
done

if [ "$SERVER_RESPONDING" = false ]; then
    echo "경고: API 서버가 제한 시간 내에 응답하지 않습니다. 로그를 확인하세요: $LOG_FILE"
    echo "Gradio 실행을 시도하지만, 서버가 준비되지 않았을 수 있습니다."
    # 필요시 여기서 종료
    # echo "VLLM 서버 시작 실패. 스크립트를 종료합니다."
    # exit 1
fi

# 서버가 응답하면 실제 API 호출로 모델 로딩 완료 확인 (선택적 강화)
if [ "$SERVER_RESPONDING" = true ]; then
    echo "모델이 요청을 처리할 수 있는지 확인합니다..."
    MAX_API_RETRIES=90 # 7.5분 (90 * 5초)
    API_RETRY_COUNT=0
    MODEL_LOADED=false

    while [ $API_RETRY_COUNT -lt $MAX_API_RETRIES ]; do
        RESPONSE_CODE=$(curl -s -X POST "http://localhost:7000/v1/chat/completions" \
           -H "Content-Type: application/json" \
           -d "{\"model\":\"$MODEL_TO_SERVE\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":1}" \
           -o /dev/null -w "%{http_code}")
        
        if [ "$RESPONSE_CODE" -eq 200 ]; then
            echo "모델 로딩 완료! API 테스트 요청 성공 (HTTP 200)."
            MODEL_LOADED=true
            break
        else
            echo "모델 API 테스트 요청 실패 (HTTP $RESPONSE_CODE). VLLM 로그($LOG_FILE)를 확인하세요. 대기 중... ($API_RETRY_COUNT/$MAX_API_RETRIES)"
        fi
        
        API_RETRY_COUNT=$((API_RETRY_COUNT + 1))
        sleep 5
    done
    
    if [ "$MODEL_LOADED" = false ]; then
        echo "경고: 모델이 테스트 API 호출에 응답하지 않았습니다. 로그를 확인하세요: $LOG_FILE"
    else
        echo "VLLM 서버가 완전히 준비되었습니다!"
    fi
fi

# Gradio 앱 시작
echo "2. Gradio 웹 인터페이스 시작 중..."
echo "====== Gradio 시작 ======"

# 현재 디렉토리를 frontend로 변경
cd "/root/frontend" || exit 1
echo "현재 디렉토리: $(pwd)"

# Gradio 앱 실행
python "$GRADIO_APP_PATH" \
    --api-base-url "http://localhost:7000" \
    --port 8000 # Gradio 포트

echo "Gradio 웹 인터페이스가 시작 되었씁니다"

# 종료 시 VLLM 서버도 함께 종료
echo ""
echo "애플리케이션을 종료합니다..."
if [ -f "$PID_FILE" ] && ps -p $(cat "$PID_FILE") > /dev/null; then
    STORED_PID=$(cat "$PID_FILE")
    echo "VLLM 서버 종료 중 (PID: $STORED_PID)..."
    kill "$STORED_PID"
    rm "$PID_FILE"
    echo "VLLM 서버가 종료되었습니다."
else
    echo "VLLM 서버 PID 파일을 찾을 수 없거나 프로세스가 이미 종료되었습니다."
fi

echo "====== 스크립트 종료 ======" 