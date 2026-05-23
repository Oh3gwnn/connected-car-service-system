# 📊 LMS(Log Management System) 기반 CCS 검증 스펙 정의서

이 문서는 실무 수동 및 자동화 검증 시 시각적 로그 추적과 데이터 정합성을 증명하기 위해 개발된 **가상 LMS 모니터링 시스템**의 명세서입니다.

## 1. 주요 검증 도메인 약어 정의

* **RSC (Remote Start & Climate):** 원격 시동 및 공조 제어 서비스 도메인
* **VSS (Vehicle Status Service):** 차량 상태 주기 보고 및 원격 도어 제어 도메인
* **MRC-A (Mobile Remote Control Type-A):** 스마트폰 모바일 앱에서 직접 요청을 시작하는 제어 트랜잭션 타입

## 2. 실무형 통합 상태 코드 구조 (Custom Status Codes)

커넥티드 카 통신망에서 트랜잭션의 상세 결과를 세부 식별하고, 수동/자동화 테스트 시 원인을 즉각 파악할 수 있도록 도메인별로 상태 코드를 체계화합니다.

### 🟢 2000 계열: E2E 제어 최종 성공 (Success)

| 상태 코드 | 식별자 (ID) | 설명 | 테스트 대상 제어기 |
| :--- | :--- | :--- | :--- |
| **`2000`** | `RSC_CLIMATE_SUCCESS` | 원격 공조 가동 완료 및 설정 온도 도달 성공 | CCU ➡️ BCM ➡️ FATC |
| **`2010`** | `RSC_ENGINE_SUCCESS` | 원격 시동(Engine Start-up) 물리 기동 성공 | CCU ➡️ BCM ➡️ EMS |
| **`2040`** | `VSS_LOCK_SUCCESS` | 원격 도어 잠금(Door Lock) 최종 완료 | CCU ➡️ BCM |
| **`2041`** | `VSS_UNLOCK_SUCCESS` | 원격 도어 잠금 해제(Door Unlock) 최종 완료 | CCU ➡️ BCM |

### 🟡 4000 계열: 서버 및 프로비저닝 검증 오류 (Validation Guards)

| 상태 코드 | 식별자 (ID) | 설명 | 테스트 대상 제어기 |
| :--- | :--- | :--- | :--- |
| **`4000`** | `REQ_INVALID_PARAM` | 제어 필수 요청 파라미터(온도 등) 누락 및 규격 오류 | Telematics Server |
| **`4002`** | `REQ_ECO_LIMIT` | 차종 유종 타입 제약 가드 (EV차량의 물리 시동 요청 등) | Telematics Server |
| **`4030`** | `PROV_UNREGISTERED` | 미등록 차대번호(VIN) 정보 접근 차단 | Telematics Server |
| **`4031`** | `PROV_DEACTIVATED` | 개통 해지 및 통신 서비스 만료 단말 차단 | Telematics Server |

### 🔴 7000 계열: 무선 구간 및 차량 CAN 네트워크 오류 (Network/Bus Fail)

| 상태 코드 | 식별자 (ID) | 설명 | 테스트 대상 제어기 |
| :--- | :--- | :--- | :--- |
| **`7000`** | `TBOX_TIMEOUT` | 단말기(T-Box/CCU) 응답 수신 시간 대기 초과 (Timeout) | CCU (T-Box) |
| **`7930`** | `CAN_TX_FAIL_TEMP` | 설정 온도 한계 범위 초과로 내부 B-CAN 송출 거부 | CCU ➡️ CAN Bus |
| **`7931`** | `CAN_TX_FAIL_CMD` | 차량 유선 내부 CAN 버스 통신 물리 패킷 전송 실패 | CCU ➡️ CAN Bus |

### 🔒 8000 계열: 보안 및 무선 데이터 무결성 오류 (Security Fail)

| 상태 코드 | 식별자 (ID) | 설명 | 테스트 대상 제어기 |
| :--- | :--- | :--- | :--- |
| **`8001`** | `DECRYPT_FAIL` | 무선 전송 패킷의 복호화 실패 및 무결성 서명 오류 | CCU (Secure Module) |

## 3. 4단계 퀄리티 어슈어런스 (QA) 수동 체크리스트

수동 테스터로서 기능을 PASS 처리하기 위해 매번 체크해야 하는 리포트 항목입니다:

1. **LMS 사다리 그래프 흐름 검증:** App ➡️ Server ➡️ Broker ➡️ CCU ➡️ ECU로 이어지는 실시간 순차 전송이 정상 유효 기한 내에 진행되는지 여부
2. **Raw Log 대조:** FastAPI의 텔레메트리 데이터와 차량 터미널의 가상 바이트 정보 일치 여부
3. **차량 단말 상태 천이 검증:** 공조 명령 후 가상 CCU 및 ECU의 변수(온도, 시동 상태) 실제 변경 여부
4. **폰앱 알림 푸시 검증:** 트랜잭션 완료 시 스마트폰 팝업창으로 고유 코드와 성공 문구가 도출되는지 여부