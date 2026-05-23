import json
import os
import platform
import sys
import paho.mqtt.client as mqtt

# -----------------------------------------------------------------------------
# [환경 설정 및 변수]
# -----------------------------------------------------------------------------
# Docker 컨테이너 외부/내부 환경에 유연하게 대응하기 위해 환경변수를 참조합니다.
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))

# -----------------------------------------------------------------------------
# [OS 감지 및 python-can 버스 어댑터 설정]
# -----------------------------------------------------------------------------
CAN_SUPPORTED = False
try:
    import can
    CAN_SUPPORTED = True
except ImportError:
    print("⚠️  [CCU] 'python-can' 라이브러리가 존재하지 않습니다. 수동 시뮬레이션 로그 모드로 구동합니다.")

def init_can_interface():
    """
    운영체제(OS) 환경을 스스로 감지하여 최적의 CAN 드라이버를 로드합니다.
    - Linux: SocketCAN 드라이버의 실제 'vcan0' 버스에 연결을 시도합니다.
    - macOS / Windows: 테스트가 가능하도록 인메모리 가상 버스('virtual' 드라이버)로 우회합니다.
    """
    if not CAN_SUPPORTED:
        return None

    current_os = platform.system().lower()
    print(f"ℹ️  [CAN 시스템] 감지된 운영체제 환경: {current_os.upper()}")

    try:
        if current_os == "linux":
            # 실제 리눅스(오라클 클라우드 포함) 환경용 SocketCAN 바인딩 (최신 interface 인자 사용)
            bus = can.interface.Bus(channel="vcan0", interface="socketcan")
            print("🟢 [CAN 시스템] Linux SocketCAN 활성화: 'vcan0' 버스에 연결되었습니다.")
            return bus
        else:
            # macOS, Windows용 python-can 내부 virtual 드라이버 바인딩 (최신 interface 인자 사용)
            bus = can.interface.Bus(channel="vcan0_virtual", interface="virtual")
            print("🟡 [CAN 시스템] 비리눅스 환경: python-can 'virtual' 드라이버로 'vcan0_virtual' 버스를 켭니다.")
            return bus
    except Exception as e:
        print(f"❌ [CAN 시스템] 버스 초기화 실패: {str(e)}")
        print("⚠️  [CAN 시스템] 로컬 모의 로그 출력 모드로 시뮬레이션을 대체합니다.")
        return None

# 전역 가상 CAN 버스 인스턴스 초기화
can_bus = init_can_interface()

# -----------------------------------------------------------------------------
# [MQTT 통신 이벤트 콜백]
# -----------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc, properties=None):
    """
    MQTT 브로커에 접속을 성공했을 때 호출되는 콜백 함수입니다.
    """
    if rc == 0:
        print("🟢 [CCU] MQTT 브로커 연결 완료. 무선 제어 통신 대기 상태입니다.")
        # 차대번호(VIN)에 관계없이 들어오는 모든 제어 요청을 분석하기 위해 단일 레벨 와일드카드(+) 사용
        topic = "ccs/vehicle/+/control"
        client.subscribe(topic)
        print(f"📡 [CCU] 구독 토픽 활성화: {topic}")
    else:
        print(f"❌ [CCU] 브로커 연결 실패 (에러 코드: {rc})")

def on_message(client, userdata, msg):
    """
    MQTT 브로커로부터 특정 차량을 대상으로 한 원격 명령 데이터가 감지되면 처리합니다.
    """
    topic = msg.topic
    payload_str = msg.payload.decode("utf-8")

    # 토픽에서 차대번호(VIN) 동적 분리 (ccs/vehicle/{vin}/control)
    topic_parts = topic.split("/")
    if len(topic_parts) < 4:
        print(f"⚠️  [CCU] 비표준 구조 토픽 수신: {topic}")
        return

    vin = topic_parts[2]

    print("\n" + "="*70)
    print(f"📡 [CCU 무선 수신] 서버에서 무선 원격 제어 요청 포착!")
    print(f"├─ 대상 VIN : {vin}")
    print(f"└─ 메시지   : {payload_str}")

    try:
        data = json.loads(payload_str)
        command = data.get("command")
        temperature = data.get("temperature")

        if not command:
            print("❌ [CCU 검증 에러] 필수 필드 'command'가 유실되어 수신 신호를 드롭합니다.")
            return

        # 무선 원격 데이터 -> 차량 내부 유선 CAN 프레임으로 변환 수행
        transmit_can_signal(command, temperature)

    except json.JSONDecodeError:
        print("❌ [CCU 에러] 무선 수신 패킷이 올바른 JSON 포맷이 아닙니다.")
    except Exception as e:
        print(f"❌ [CCU 에러] 메시지 디코딩 처리 중 장치 오류 발생: {str(e)}")

def transmit_can_signal(command, temperature):
    """
    검증이 통과된 텔레매틱스 명령을 바이트 페이로드로 변환하여 내부 B-CAN 버스(vcan0)에 송출합니다.
    """
    can_id = 0x123  # 바디 제어용 가상 CAN ID 정의
    data_bytes = [0x00] * 8  # 8바이트 기본 버퍼 매핑

    # 바이트 매핑 규칙 설계 (Byte 0: 원격 제어 구분자, Byte 1: 명령 코드, Byte 2: 설정 온도 축소 8비트 정수)
    data_bytes[0] = 0x01  # 0x01: 원격 제어 모드 활성 플래그

    if command in ["START_CLIMATE", "START_ENGINE"]:
        data_bytes[1] = 0x01  # 0x01: 시동 및 공조 온(ON) 명령 코드
        if temperature is not None:
            # DBC 팩터 적용 시뮬레이션: 온도를 0.5단위 정밀도로 표현하기 위해 * 2로 인코딩하여 전송
            # 예: 23.5도 전송 시 -> 23.5 * 2 = 47 (Hex: 0x2F) 바이트 변환
            data_bytes[2] = int(temperature * 2)
    elif command == "STOP_CLIMATE":
        data_bytes[1] = 0x02  # 0x02: 공조 오프(OFF) 명령 코드
    elif command == "LOCK_DOOR":
        can_id = 0x201       # 섀시/바디 도어 통합 CAN ID 변경 모사
        data_bytes[1] = 0x03  # 0x03: 문 잠금 명령 코드
    elif command == "UNLOCK_DOOR":
        can_id = 0x201
        data_bytes[1] = 0x04  # 0x04: 문 열림 명령 코드
    else:
        print(f"⚠️  [CCU] 매핑되지 않은 명령 패턴입니다: {command}")
        return

    print(f"⚡ [CCU -> B-CAN] 패킷 인코딩 완료 (ID: {hex(can_id)}, Payload: {[hex(x) for x in data_bytes]})")

    # 가상 혹은 실제 활성화된 CAN 인터페이스로 프레임 브로드캐스팅 시도
    if can_bus:
        try:
            msg = can.Message(
                arbitration_id=can_id,
                data=data_bytes,
                is_extended_id=False
            )
            can_bus.send(msg)
            print("🚀 [CCU -> B-CAN] 로컬 가상 CAN 인터페이스를 통해 패킷 물리 송출을 성공했습니다.")
        except Exception as e:
            print(f"❌ [CCU -> B-CAN] 패킷 송출 실패 (드라이버 에러): {str(e)}")
    else:
        print("💻 [CCU (가상 로그 모드)] python-can 비활성화 상태로, 가상 변환 흐름만 터미널에 시뮬레이션 출력합니다.")
    print("="*70)

# -----------------------------------------------------------------------------
# [모듈 구동 메인 엔진]
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Paho MQTT Client v2 규격 설정 적용
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        print("🚗 [CCU Simulator] 가상 차량 게이트웨이 및 무선 수신기(CCU) 구동을 시작합니다...")
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n🛑 [CCU Simulator] 수동 제어 모니터링 인터페이스 작동을 안전하게 중지합니다.")
    except Exception as e:
        print(f"❌ [CCU Simulator] 구동 중 예기치 않은 시스템 예외 발생: {str(e)}")