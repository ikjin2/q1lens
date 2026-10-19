# set_cond 직접 확인하기

각 하위 폴더는 독립된 Q1Lens 프로젝트입니다. Q1ASM 파일을 열고 편집기에서
우클릭 → **Q1Lens: Open Timeline Preview**를 실행하면 같은 폴더의
`q1timeline.yml`을 사용합니다. 다른 예제로 이동할 때도 해당 파일에서 실행하세요.
실행 모드 설정이 `project`이면 각 YAML의 `analysis.execution_mode`를 따릅니다.

## 1. 조건 해제와 긴 wait 접기

열 파일: [01-disabled/disabled.q1asm](01-disabled/disabled.q1asm)

타임라인의 **▶ Run execution**을 누르세요.

- `set_cond 0,1,7,20`은 조건 해제이므로 operator 7이어도 통과합니다.
- 바로 다음 register 형식도 enable=0이므로 미정인 R4/R5가 실행을 막지 않습니다.
- `wait 65535`가 3번 실행되고, 루프와 긴 wait가 기본으로 접혀야 합니다.
- 소스의 wait를 선택해도 루프가 저절로 펼쳐지지 않아야 합니다.
- 최종 RT 시간은 **196613 ns**, marker는 **1 → 3**입니다.

## 2. RT마다 조건을 다시 평가하기 — 먼저 볼 예제

열 파일: [02-trigger-execution/trigger_execution.q1asm](02-trigger-execution/trigger_execution.q1asm)

이 프로젝트는 처음부터 `simulation` 모드로 분석합니다. 타임라인을 연 직후의
결과를 확인하세요. 위쪽의 **▶ Run execution**은 별도의 분석 재생 기능이므로,
활성 `set_cond`에서 멈춥니다. 그 경우 **Refresh**로 시뮬레이션 결과를 다시
불러오세요. Hardware → **QCM0 → trigger_demo**에서 시간 cursor를 옮기거나
기록된 실행 replay를 사용해 확인할 수 있습니다.

| RT 시각 | 예상 결과 |
| --- | --- |
| 200–204 ns | 첫 `upd_param`: marker=1, frequency=1 MHz 적용 |
| 204–208 ns | `set_latch_en`: trigger counter 활성화 |
| 208–248 ns | 첫 조건부 `upd_param 16`: 조건이 거짓이므로 **40 ns 건너뜀**. marker=1, frequency=1 MHz 유지 |
| 230 ns | YAML에 지정한 address 1 trigger 도착. 이미 건너뛰는 명령은 계속 건너뜀 |
| 248–264 ns | 두 번째 `upd_param 16`: 조건을 다시 평가해 참. marker=3, frequency=2 MHz 적용 |
| 264–280 ns | 조건 해제 후 마지막 update: marker=2, frequency=3 MHz 적용 |

`move 7,R0`은 Q1 명령이므로 조건에 관계없이 실행되어 R0=7이 됩니다.
Skipped 블록의 captured 값이 실제 applied 값으로 보이면 안 됩니다.
Hardware의 frequency 원시값은 **4000000 → 8000000 → 12000000**입니다.
[`set_freq`의 단위가 0.25 Hz](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#set-freq)이므로
각각 1, 2, 3 MHz에 해당합니다.

`q1timeline.yml`의 trigger `time_ns`를 **260**으로 바꾸면 두 조건부 update가
모두 건너뛰어지고, 마지막 update만 **288–304 ns**에 실행됩니다.
trigger 시간을 **220**으로 바꾸면 기본값 230과 같은 결과입니다.

여기서 startup 200 ns와 trigger 시각은 시나리오 입력입니다. 장비의 초기
prefill 시간을 측정하거나 확정한 값이 아닙니다. trigger threshold=1,
반전 없음이라는 현재 시뮬레이터 가정을 사용합니다.

## 3. 루프 속 조건 변경과 미정 시간

열 파일: [03-changing-loop/changing_condition.q1asm](03-changing-loop/changing_condition.q1asm)

이 프로젝트는 정적 `analysis` 모드입니다. 첫 반복은 enable=0이지만, 두 번째는
R0=1이 됩니다. 첫 반복의 8 ns를 그대로 두 번 더해 정확한 시간처럼 표시하면
안 됩니다. 루프 시간과 이후 `wait 4`의 절대 시각은 **unknown**이어야 합니다.

- **Time scale**: 알려진 첫 반복은 보이고, 이후 RT 명령이 0 ns에 겹치지 않습니다.
- **Instruction**: 시간이 미정인 명령도 프로그램 순서대로 보입니다.
- 마지막에 조건을 꺼도 이미 불확실해진 누적 시간이 복구되지는 않습니다.
- 일반 ▶ 재생은 두 번째 반복의 활성 `set_cond`에서 멈춥니다.

YAML의 `execution_mode`를 **simulation**으로 바꾸고 저장하면 trigger 입력이
없으므로 두 번째 반복은 20 ns 건너뜁니다. 이 모델에서 RT는
**200–208 ns 실행 → 208–228 ns 건너뜀 → 228–232 ns wait**, 총 232 ns에
끝납니다. 다시 `analysis`로 돌리면 보수적인 정적 표시와 비교할 수 있습니다.

## 4. 잘못된 else_duration 거부

열 파일: [04-invalid-else/invalid_else.q1asm](04-invalid-else/invalid_else.q1asm)

의도적으로 `-1`을 넣은 오류 예제입니다. 해당 줄에 `invalid_argument_value`
진단이 나고 RT 명령을 실행하지 않아야 합니다. 음수 시간으로 진행하면 안 됩니다.
`-1`을 **4**로 바꾸면 이 입력 오류가 사라집니다.
