import time
import requests
import pytest

SIM_URL = "http://127.0.0.1:8050"

@pytest.fixture(autouse=True)
def clean_start():
    """테스트 시작 전 무조건 B+ 리셋을 날려 완벽한 SLEEP 사전 상태를 세팅"""
    res = requests.post(f"{SIM_URL}/hardware/signal", json={"signal": "BPLUS_RESET"})
    assert res.status_code == 200
    yield

def test_automotive_integrated_spec_evaluation():
    print("\n\n" + "="*80)
    print("📋 [AUTOMOTIVE TC] 차량 전원 천이 사양 및 MT/MO 통합 자동화 검증")
    print("="*80)

    # -------------------------------------------------------------------------
    # 1단계: 사전 조건 (Pre-conditions) 확실하게 검증
    # -------------------------------------------------------------------------
    print("\n🔍 [STEP 1] 사전 전원 조건 검증 시작")
    pre_st = requests.get(f"{SIM_URL}/hardware/status").json()
    assert pre_st["panel_acc"] == 0, "Panel ACC 사전 오염 실패"
    assert pre_st["wire_acc"] == 0, "Wire ACC 사전 오염 실패"
    assert pre_st["power_state"] == "SLEEP"
    assert pre_st["sleepmode"] == 1
    print("🟢 [PASS] 사전 상태 정합성 완벽 확인 (Deep Sleep 상태 인증)")

    # -------------------------------------------------------------------------
    # 2단계: 웨이크업 시퀀스 기동 및 5초 부팅 계측
    # -------------------------------------------------------------------------
    print("\n⚙️  [STEP 2] 전원 동시 인가 및 5초 가상 부팅 딜레이 추적")
    start_time = time.time()
    trig_res = requests.post(f"{SIM_URL}/hardware/signal", json={"signal": "WAKEUP_TRIGGER"}).json()
    assert trig_res["status"] == "TRANSITION_STARTED"
    
    # READY 상태(부팅 완료)가 될 때까지 0.5초 단위 폴링 대기
    is_ready = False
    while time.time() - start_time < 8:
        st = requests.get(f"{SIM_URL}/hardware/status").json()
        if st["power_state"] == "READY":
            is_ready = True
            break
        time.sleep(0.5)
        
    assert is_ready is True, "5초 부팅 사양서 시간 초과 타임아웃 오류"
    boot_latency = time.time() - start_time
    print(f"🟢 [PASS] 가상 커널 부팅 완료 자동 추적 성공 (소요 시간: {boot_latency:.2f}초)")

    # -------------------------------------------------------------------------
    # 3단계: MO 통보 - VSS 상태 코드 데이터 정합성 검증 (핵심!!)
    # -------------------------------------------------------------------------
    print("\n📊 [STEP 3] MO 통보 데이터 내 요구사항 핀 및 디폴트 유지 검증")
    post_st = requests.get(f"{SIM_URL}/hardware/status").json()
    
    # 켜져야 하는 값 확인
    assert post_st["panel_acc"] == 1, "Panel ACC 켜짐 로그 누락"
    assert post_st["wire_acc"] == 1, "Wire ACC 릴레이 활성화 실패"
    assert post_st["sleepmode"] == 0, "sleepmode 가 WAKEUP(0)으로 전환되지 않음"
    
    # ★ 회원님이 강조하신 핵심: 켜지면 안 되는 핀들은 오염 없이 0(디폴트)을 유지해야 함
    assert post_st["ign1"] == 0, "❌ [CRITICAL] IGN1 핀이 디폴트(0)를 유지하지 못하고 오염됨!"
    assert post_st["ign3"] == 0, "❌ [CRITICAL] IGN3 핀이 디폴트(0)를 유지하지 못하고 오염됨!"
    print("🟢 [PASS] VSS 상태 데이터 완벽 일치: [sleepmode=0 / acc=1 / ign1,3=0(디폴트 유지 확인)]")

    # -------------------------------------------------------------------------
    # 4단계: MT 제어 검증 (Remote Door Open / Remote Status Check)
    # -------------------------------------------------------------------------
    print("\n🔓 [STEP 4] 서버 ➔ 차량 원격 MT 제어 사양 검증 (RDO / RSC)")
    
    # RDO(문열림 명령) 인입 시 문 락 상태가 0(해제)으로 변하는가?
    rdo_res = requests.post(f"{SIM_URL}/hardware/remote", json={"signal": "", "command": "RDO"}).json()
    assert rdo_res["status"] == "RDO_SUCCESS"
    assert rdo_res["door_locked"] == 0
    
    # RSC(상태체크 명령) 인입 시 차량의 레지스터를 깨끗하게 반환하는가?
    rsc_res = requests.post(f"{SIM_URL}/hardware/remote", json={"signal": "", "command": "RSC"}).json()
    assert rsc_res["status"] == "RSC_SUCCESS"
    assert rsc_res["current_vehicle_reg"]["power_state"] == "READY"
    print("🟢 [PASS] MT 원격 제어 시퀀스 정상 수용 확인 (RDO 도어 오픈 & RSC 레지스터 체크 완료)")

    # -------------------------------------------------------------------------
    # 5단계: MO 통보 - SVN (B-Alarm 보안 도난 경보) 검증 및 최종 마감
    # -------------------------------------------------------------------------
    print("\n🚨 [STEP 5] 차량 주도 MO 통보 사양 검증 (SVN B-Alarm)")
    svn_res = requests.post(f"{SIM_URL}/hardware/signal", json={"signal": "TRIGGER_BALARM"}).json()
    assert svn_res["status"] == "SVN_SENT"
    assert "transaction_id" in svn_res
    
    print("\n" + "="*80)
    print(f"🏆 [FINAL RESULT] INTEGRATED EVALUATION MATRIX STATUS ➔ PASSED")
    print("="*80 + "\n")