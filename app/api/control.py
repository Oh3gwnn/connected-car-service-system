import json
from typing import Optional # 구버전 파이썬 호환성을 위해 추가
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
import paho.mqtt.client as mqtt

router = APIRouter(prefix="/control", tags=["Remote Control"])

# 1. 요청 데이터 구조 정의 (Pydantic DTO)
class ControlRequest(BaseModel):
    vin: str = Field(..., description="차량 고유 차대번호 (17자리)", example="KMHCT41BPJU123456")
    command: str = Field(..., description="제어 명령 (START_CLIMATE, STOP_CLIMATE, LOCK_DOOR, UNLOCK_DOOR)", example="START_CLIMATE")
    # float | None 대신 Optional[float]을 사용하여 모든 파이썬 버전에서 동작하도록 수정
    temperature: Optional[float] = Field(None, description="설정 온도 (공조 제어 시 필수)", example=23.5)

# 2. MQTT 메시지 발행 함수
def publish_mqtt_message(topic: str, payload: dict):
    try:
        # 로컬 도커 브로커(Port: 1883)에 연결
        client = mqtt.Client(mqtt.Client.CallbackAPIVersion.VERSION2)
        client.connect("localhost", 1883, 60)
        
        # 딕셔너리 페이로드를 JSON 문자열로 인코딩하여 발행
        client.publish(topic, json.dumps(payload))
        client.disconnect()
    except Exception as e:
        print(f"❌ [MQTT] 브로커 연결 및 메시지 발행 실패: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="차량 제어 장치(MQTT 브로커)와의 통신에 실패했습니다."
        )

# 3. 원격 제어 엔드포인트 정의
@router.post("/vehicle", status_code=status.HTTP_202_ACCEPTED)
async def remote_control_vehicle(request: ControlRequest):
    print(f"📱 [FastAPI 서버 수신] VIN: {request.vin} | 명령: {request.command} | 온도: {request.temperature}도")
    
    # 예외 처리: 공조 제어 명령인데 온도가 빠진 경우
    if "CLIMATE" in request.command and request.temperature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="공조 제어(CLIMATE) 명령에는 설정 온도(temperature)가 필수입니다."
        )
        
    # 차량으로 송신할 MQTT 페이로드 및 토픽 조립
    mqtt_payload = {
        "command": request.command,
        "temperature": request.temperature
    }
    mqtt_topic = f"ccs/vehicle/{request.vin}/control"
    
    # MQTT 브로커로 신호 토스
    publish_mqtt_message(mqtt_topic, mqtt_payload)
    print(f"📡 [FastAPI -> MQTT 발행] 토픽: {mqtt_topic} | 전송 완료")
    
    return {
        "status": "ACCEPTED",
        "vin": request.vin,
        "message": f"차량({request.vin})으로 {request.command} 제어 신호가 송출되었습니다."
    }