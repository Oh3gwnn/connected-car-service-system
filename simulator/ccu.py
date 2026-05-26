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

# 로컬 피드백 채널 주소
TELEMATICS_SERVER_URL = "http://127.0.0.1:8000/api/v1/telematics/status"

# 🌟 [피드백 완벽 반영] 차량의 물리 상태를 기억하는 실시간 로컬 레지스터
# (Change-only 감지를 통하여 중복 요청 시 VSS 동기화를 지능적으로 필터링)
CURRENT_STATE = {
    "ign_state": 0,          # 0: PANEL_OFF, 1: ACC, 2: IGN_ON, 3: KEY_START
    "lock": 1,               # 0: UNLOCKED, 1: LOCKED
    "climate_active": 0,     # 0: OFF, 1: ACTIVE
    "target_temp": 22.0
}

# -----------------------------------------------------------------------------
# [OS 감지 및 python-can 버스 어댑터 설정]
# -----------------------------------------------------------------------------
CAN_SUPPORTED = False
try:
    import can
    CAN_SUPPORTED = True
except ImportError:
    print("⚠️  [CCU] 'python-can' 라이브러리가 없어 가상 시뮬레이션 모드로 작동합니다.")

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

        # 1차 로컬 데이터 유효성 검증
        if not command:
            report_back_to_server(tx_id, vin, 7000, "7000: 단말 데이터 규격 해독 실패")
            return

        # 차량 로컬 온도 제한 정책 적용 -> 7000 에러 코드로 통일
        if "CLIMATE" in command and temperature is not None:
            if not (16.0 <= temperature <= 30.0):
                print(f"❌ [CCU 검증] 비정상 범위 공조 요구 에러 ({temperature}°C)")
                report_back_to_server(tx_id, vin, 7000, f"7000: B-CAN 송출 거부 - 한계 온도 초과 ({temperature}°C)")
                return

        # 🌟 [피드백 반영] 이진 상태 감지 로직 가동
        # 이전 상태와 대조하여 실제 변동 여부를 판별합니다.
        has_changed = False
        if "RDO_UNLOCK" in command:
            if CURRENT_STATE["lock"] != 0:
                CURRENT_STATE["lock"] = 0
                has_changed = True
        elif "RDO_LOCK" in command:
            if CURRENT_STATE["lock"] != 1:
                CURRENT_STATE["lock"] = 1
                has_changed = True
        elif "CLIMATE" in command:
            if CURRENT_STATE["ign_state"] != 3 or CURRENT_STATE["climate_active"] != 1 or (temperature is not None and CURRENT_STATE["target_temp"] != temperature):
                CURRENT_STATE["ign_state"] = 3
                CURRENT_STATE["climate_active"] = 1
                if temperature is not None:
                    CURRENT_STATE["target_temp"] = temperature
                has_changed = True
        elif "ENGINE" in command:
            if CURRENT_STATE["ign_state"] != 3:
                CURRENT_STATE["ign_state"] = 3
                has_changed = True

        # 무선 ➡️ 유선 CAN 프레임 변환 송출
        success = transmit_can_signal(command, temperature)
        
        if success:
            # 🌟 [핵심 변경사항] 상태가 실제 변동된 경우에만 VSS Status Sync (204)를 선제 보고합니다!
            if has_changed:
                print("🔄 [CCU ➔ VSS] 차량 정보 변동 감지! VSS 동기화 패킷 전송을 트리거합니다.")
                vss_msg = "VSS Sync: "
                if "UNLOCK" in command:
                    vss_msg += "RDO lock=0 (UNLOCKED)"
                elif "LOCK" in command:
                    vss_msg += "RDO lock=1 (LOCKED)"
                elif "CLIMATE" in command:
                    vss_msg += f"RSC ign=3, climate_active=1, temp={temperature}°C"
                elif "ENGINE" in command:
                    vss_msg += "RSC ign=3 (KEY_START)"
                
                report_back_to_server(tx_id, vin, 204, vss_msg)
            else:
                print("ℹ️ [CCU ➔ VSS] 차량 정보 변동 없음 (0 ➔ 0 / 1 ➔ 1). VSS 동기화 생략.")

            # 🌟 MT-MO 트랜잭션의 최종 완성은 항상 200 (MRC_SUCCESS)으로 마감
            mrc_msg = "RDO 문 제어 성공" if "RDO" in command else f"RSC 원격 제어 {temperature if temperature else ''}°C 완료"
            report_back_to_server(tx_id, vin, 200, mrc_msg)
        else:
            report_back_to_server(tx_id, vin, 7000, "7000: 유선 내부 CAN 버스 송출 실패")

    except json.JSONDecodeError:
        print("❌ [CCU] JSON 포맷 해석 실패")
        report_back_to_server(tx_id, vin, 7000, "7000: 패킷 해석 실패 (JSON syntax)")
    except Exception as e:
        print(f"❌ [CCU] 장치 가드 에러: {str(e)}")
        report_back_to_server(tx_id, vin, 7000, f"7000: 시스템 장치 에러: {str(e)}")

def transmit_can_signal(command, temperature):
    can_id = 0x123
    data_bytes = [0x00] * 8
    data_bytes[0] = 0x01

    if "CLIMATE" in command or "ENGINE" in command:
        data_bytes[1] = 0x01
        if temperature is not None:
            data_bytes[2] = int(temperature * 2)
    elif "LOCK" in command:
        can_id = 0x201
        data_bytes[1] = 0x03
    elif "UNLOCK" in command:
        can_id = 0x201
        data_bytes[1] = 0x04

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
        print("💻 [CCU (가상 모드)] 가상 시뮬레이터 인터페이스 정상 출력 완료.")
        return True

def report_back_to_server(tx_id, vin, status_code, message):
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