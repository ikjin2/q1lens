# Q1Lens 시뮬레이션 검증

`q1lens-simulation.code-workspace`를 열면 이 예제들만 표시됩니다. Python 경로는
`python`으로 설정되어 있습니다. 다른 환경을 사용하려면 workspace의
`q1lens.q1timeline.pythonPath`와 `python.defaultInterpreterPath`를 변경하세요.

## 먼저 자동 검증

VS Code에서 **Terminal → Run Task → Q1Lens: Verify all simulation examples**를
실행하거나 이 폴더에서 다음 명령을 실행하세요.

```powershell
python verify.py
```

다섯 사례를 각각 두 번 실행하여 기대값과 재현성을 확인합니다. 결과는
`.verification/report.json`, 각 timeline은 `<예제>/.q1timeline/timeline_ir.json`에
저장합니다. `04-underflow`는 의도적으로 실패하는 프로그램이며, **24 ns의
underflow를 검출해야 검증 PASS**입니다.

## VS Code에서 확인하는 순서

1. 아래 표의 Q1ASM 파일을 열고 **Q1Lens: Open Timeline Preview**를
   실행하세요. 다른 예제로 이동할 때는 파일을 우클릭하여 해당 preview 명령을
   실행하면 그 파일의 `q1timeline.yml`을 선택합니다.
2. **Hardware** 화면에서 Module → Sequencer를 선택하세요.
3. 시간 cursor와 이전/다음 step으로 이동합니다. **Registers**에서는 값의 변경,
   readable 시각, 이전 값, 입력 register와 소스 명령을 확인합니다.
4. **RT queue / Simulation**에서 실제 queue depth, Q1 stall, feedback·trigger
   도착을 확인합니다. 이벤트 버튼으로 그 시각에 이동할 수 있습니다.
5. 신호 예제의 **Simulation**에서 stage를 바꿔 waveform, gain/offset, NCO,
   filter, DAC, RF envelope, ADC를 비교합니다. **Bins**에서는 완료된 I/Q 적분값과
   threshold, 입력 출처를 확인합니다.

| 예제 / 열 파일 | 확인할 결과 |
| --- | --- |
| `01-digital/counter.q1asm` | R1: 0 → 1 → 2 → 3, R2=3. Queue capacity=2, queue-full stall 발생. Pending gain과 실제 적용 시각이 다름. |
| `02-communication/receiver.q1asm` | sender가 204 ns에 전송, 24 ns 설정 지연 후 228 ns에 수신. R0=7, R1=8. Trigger는 1100 ns에 도착. |
| `03-signals/readout.q1asm` | drive: 204 ns 파형 시작. readout: 208–272 ns 적분, 평균 I≈0.4993, I 적분≈31.9531 FS·ns, Q=0, threshold=1. controller: R0=3(valid+result), R1=1. |
| `04-underflow/starved.q1asm` | RT가 20–24 ns의 wait를 소비하는 동안 Q1은 입력을 기다림. 24 ns underflow와 부분 trace 확인. |
| `05-supplied-input/readout.q1asm` | 제공된 8개 샘플의 I 적분=1, Q 적분=-2 FS·ns. 평균은 0.125, -0.25. 출처는 Supplied trace. |

`03-signals/q1timeline.yml`의 `noise_std`를 `0.02`로 바꾸고 저장하면 ADC 곡선과
측정값이 바뀝니다. 같은 `seed`로 다시 실행하면 같은 결과가 나옵니다. `seed`를
바꾸면 다른 잡음 표본을 사용합니다. 기본 예제 기대값을 다시 검증하려면
`noise_std: 0`으로 되돌리세요.

## 입력과 모델의 의미

- `analysis.execution_mode: simulation`은 공통 시간축의 디지털 실행입니다.
  `simulation.physical`을 지정하면 수치 신호 모델을 함께 사용합니다.
- `queue_capacity`와 `startup_delay_ns`, route의 `latency_ns`는 시나리오 설정입니다.
  예제의 200 ns startup과 20/24 ns 통신 지연은 실제 장비 측정값이 아닙니다.
- `physical.sample_period_ns`는 계산 grid입니다. waveform/weight 메모리는 1 ns
  샘플이며, export는 출력 크기 제한에 따라 간격을 줄여 표시할 수 있습니다.
- I/Q 결과는 정규화된 full scale × ns 적분값입니다. `mean_i`, `mean_q`는
  적분시간으로 나눈 값입니다. `threshold`는 회전한 **적분값**에 적용합니다.
  예를 들어 64 ns 동안 평균 0.25를 경계로 삼으려면 threshold=16입니다.
- RF는 지정한 LO 주파수의 이상적인 **복소 envelope**입니다. GHz carrier 파형,
  실제 RF 전달 특성, 양자계 동역학은 포함하지 않습니다. FIR/IIR 계수와 loopback의
  gain/delay/phase를 직접 지정할 수 있습니다.
- `05-supplied-input`은 검증용 합성 데이터입니다. 실측 I/Q 샘플을 사용하려면
  `input.i/q`, 샘플 간격·시작시각·`source`를 해당 데이터로 바꾸고
  `measured: true`로 표시하세요. 이는 사용자가 제공한 출처 표시이며 자동 장비 검증이
  아닙니다. 입력 범위 밖 샘플은 만들어내지 않습니다.

지원되지 않는 명령이나 필요한 입력이 없는 경우 `blocked`, 명령/시간 제한에
도달하면 `step_limit`, 실제 모델 오류는 `error`로 종료하고 부분 trace를 남깁니다.
`completed`는 이 모델에서 실행이 완료되었다는 의미입니다. 장비에서의 동작 보증을
뜻하지 않습니다. q1simulator 코드나 패키지는 사용하지 않습니다.

전체 설정: [simulation model reference](../../docs/simulation.md).

검증 스크립트는 실행마다 새 IR을 검사하며 필수 예제 누락과 다른 원인의 실패를
거부합니다. `python -O`/`PYTHONOPTIMIZE`에서는 검사를 생략하지 않고 즉시 실패합니다.
자동화 환경과 실행 방법은 [continuous validation](../../docs/ci-validation.md)을 참고하세요.
