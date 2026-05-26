import json
import os
import platform
import sys
import paho.mqtt.client as mqtt
import requests

# -----------------------------------------------------------------------------
# [환경 설정 및 변수]
# -----------------------------------------------------------------------------
IS_CONTAINER = os.path.exists('/.dockerenv')
DEFAULT_MQTT_HOST = "mqtt" if IS_CONTAINER else "127.0.0.1"
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", DEFAULT_MQTT_HOST)
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))

# macOS/로컬 환경에서 호스트명 확인 이슈를 방지하기 위해 서버 피드백 주소를 127.0.0.1로 고정합니다.
TELEMATICS_SERVER_URL = "http://127.0.0.1:8000/api/v1/telematics/status"

# -----------------------------------------------------------------------------
# [OS 감지 및 python-can 버스 어댑터 설정]
# -----------------------------------------------------------------------------
CAN_SUPPORTED = False
try:
    import can
    CAN_SUPPORTED = True
except ImportError:
    print("⚠️  [CCU] 'python-can' 라이브러리가 없어 시뮬레이션 로그 출력 모드로 작동합니다.")

def init_can_interface():
    if not CAN_SUPPORTED:
        return None
    current_os = platform.system().lower()
    try:
        if current_os == "linux":
            return can.interface.Bus(channel="vcan0", interface="socketcan")
        else:
            return can.interface.Bus(channel="vcan0_virtual", interface="virtual")
    except Exception as e:
        print(f"❌ [CAN] 연결 실패: {str(e)}")
        return None

can_bus = init_can_interface()

# -----------------------------------------------------------------------------
# [MQTT 통신 이벤트 콜백]
# -----------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"🟢 [CCU] MQTT 브로커 연결 완료 ({MQTT_BROKER_HOST}). 무선 제어 수신 대기 상태입니다.")
        topic = "ccs/vehicle/+/control"
        client.subscribe(topic)
        print(f"📡 [CCU] 구독 토픽 활성화: {topic}")
    else:
        print(f"❌ [CCU] 연결 실패: {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload_str = msg.payload.decode("utf-8")

    topic_parts = topic.split("/")
    if len(topic_parts) < 4:
        return

    vin = topic_parts[2]

    print("\n" + "="*70)
    print(f"📡 [CCU 무선 수신] 서버 제어 신호 포착!")
    print(f"├─ 대상 VIN : {vin}")
    print(f"└─ 메시지   : {payload_str}")

    try:
        data = json.loads(payload_str)
        tx_id = data.get("transaction_id")
        command = data.get("command")
        temperature = data.get("temperature")

        # 1차 로컬 데이터 유효성 검증 및 전압 가드 모사
        if not command:
            report_back_to_server(tx_id, vin, 8001, "단말 데이터 암호/해독 실패")
            return

        # 차량 로컬 온도 제한 정책 적용 (B-CAN 전송 전 차단)
        if "CLIMATE" in command and temperature is not None:
            if not (16.0 <= temperature <= 30.0):
                print(f"❌ [CCU 검증] 비정상 범위 공조 요구 에러 ({temperature}°C)")
                # CAN 전송을 전면 취소하고, 서버에 독자 에러코드 '7930' 송부
                report_back_to_server(tx_id, vin, 7930, f"CAN 송신 불가: 온도 에러 ({temperature}°C)")
                return

        # 무선 ➡️ 유선 CAN 프레임 변환 송출
        success = transmit_can_signal(command, temperature)
        
        # 정상 통과 시 텔레매틱스 서버에 완료 패킷 및 알림 트리거용 POST 전송
        if success:
            code = 2040 if "LOCK" in command else 2041 if "UNLOCK" in command else 2000
            msg_text = "문 잠김이 해제되었습니다" if "UNLOCK" in command else "도어가 잠겼습니다" if "LOCK" in command else f"공조기 {temperature}도 작동 완료"
            report_back_to_server(tx_id, vin, code, msg_text)

    except json.JSONDecodeError:
        print("❌ [CCU] JSON 포맷 해석 실패")
    except Exception as e:
        print(f"❌ [CCU] 장치 가드 에러: {str(e)}")

def transmit_can_signal(command, temperature):
    can_id = 0x123
    data_bytes = [0x00] * 8
    data_bytes[0] = 0x01

    if command in ["START_CLIMATE", "START_ENGINE"]:
        data_bytes[1] = 0x01
        if temperature is not None:
            data_bytes[2] = int(temperature * 2)
    elif command == "STOP_CLIMATE":
        data_bytes[1] = 0x02
    elif command in ["LOCK_DOOR", "UNLOCK_DOOR"]:
        can_id = 0x201
        data_bytes[1] = 0x03 if "LOCK" in command else 0x04

    print(f"⚡ [CCU -> B-CAN] 패킷 인코딩 완료 (ID: {hex(can_id)}, Payload: {[hex(x) for x in data_bytes]})")

    if can_bus:
        try:
            msg = can.Message(arbitration_id=can_id, data=data_bytes, is_extended_id=False)
            can_bus.send(msg)
            print("🚀 [CCU -> B-CAN] CAN 버스를 통해 ECU로 무사히 전송했습니다.")
            return True
        except Exception as e:
            print(f"❌ [CCU -> CAN] 물리 에러: {str(e)}")
            return False
    else:
        print("💻 [CCU (가상 모드)] 시뮬레이터가 유효하게 작동했습니다.")
        return True

def report_back_to_server(tx_id, vin, status_code, message):
    """차량 단말기(CCU)가 셀룰러 망을 타고 서버로 최종 제어 결과를 통보(MO Return)하는 함수"""
    print(f"📡 [CCU -> Server 통보] 트랜잭션: {tx_id} | 코드: {status_code} | 결과: {message}")
    try:
        payload = {
            "transaction_id": tx_id,
            "vin": vin,
            "status_code": status_code,
            "message": message
        }
        res = requests.post(TELEMATICS_SERVER_URL, json=payload, timeout=3)
        if res.status_code == 200:
            print("🟢 [CCU -> Server] 응답 신호 전송 완료.")
        else:
            print(f"❌ [CCU -> Server] 전송 실패: {res.status_code}")
    except Exception as e:
        print(f"❌ [CCU -> Server] 연결실패: {str(e)}")

if __name__ == "__main__":
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        print("🚗 [CCU Simulator] 가상 차량 게이트웨이 및 CCU 구동 중...")
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n🛑 [CCU Simulator] 구동 중단.")