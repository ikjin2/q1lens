# 로컬 변경 검증 예제

Q1Lens의 acquisition 버퍼 검사, 종료 시 미적용 설정 경고, 진단 UI와 저장 시
오류 표시를 확인하는 예제입니다. 장비 연결 없이 분석하며, acquisition 간격을
일괄적으로 300 ns 이상으로 제한하지 않습니다.

프로젝트를 VS Code에서 열고 Q1Lens가 설치된 Python 환경을 선택하세요.

## 확인 순서

1. 아래 사례의 `.q1asm` 파일을 엽니다. **Ctrl+Shift+P → Q1Lens: Open Timeline
   Preview**를 실행합니다. 각 폴더의 `q1timeline.yml`을 자동으로 선택합니다.
2. **Diagnostics**는 **Analysis details** 밖에 표시됩니다. 제목 옆에 진단 코드가
   중복되지 않고, 출처는 `파일명:줄`로 표시되며 일반적인 `Fix:` 문장은 없습니다.
3. **Show event** 또는 타임라인 이벤트를 누르면 **Analysis details**가 열리고
   **Selected event**, **Hardware state**를 함께 확인할 수 있습니다.
4. **Ctrl+Shift+M**으로 Problems를 열어 편집기의 경고/오류와 비교합니다.

| 사례 | 예상 결과 | 직접 바꿔볼 항목 |
| --- | --- | --- |
| `01-buffered-burst/readout.q1asm` | 4 ns 간격 7회 burst, 진단 없음 | 마지막 acquisition 아래에 2개 더 복사하고 저장하면 경고 |
| `02-short-wrap/readout.q1asm` | 마지막→첫 acquisition 간격 200 ns, 버퍼 경고 없음 | 지속 가능한 평균 부하이므로 짧은 wrap만으로 오류를 내지 않음 |
| `03-finite-pressure/readout.q1asm` | 9회 burst, 버퍼 **경고** | 뒤의 2개 acquisition을 지우고 저장하면 경고 해제 |
| `04-sustained-overload/readout.q1asm` | 확인된 QRM, 120 ns 무한 반복 → **오류** | `wait 80`을 `wait 260`으로 바꾸고 저장하면 버퍼 오류 해제 |
| `05-unknown-profile/readout.q1asm` | 같은 부하, 장비 종류 미확정 → **경고** | YAML에 `module_type: QRM`을 추가하면 오류로 변경 |
| `06-pending-settings/sequence.q1asm` | 적용되지 않은 frequency·gain **경고** | `stop`에서 **Ctrl+. → Apply pending hardware state before stop**, 저장 |
| `07-applied-settings/sequence.q1asm` | `upd_param`이 설정 적용, 진단 없음 | Hardware state에서 RT 명령에 적용된 값 확인 |
| `08-rt-buffer/sequence.q1asm` | RT underflow **오류**, `Initial buffer ≈ 1 ms` 표시 | acquisition 버퍼와 별개인 RT reserve 사례 |
| `09-unresolved-branch/readout.q1asm` | 실행·버퍼 판단 불명확 → **경고** | 간략한 Analysis is incomplete 진단과 파일명 표시 확인 |
| `10-editor-diagnostics/sequence.q1asm` | `stop` 뒤 잘못된 `move 1`도 **오류** | `move 1,R0`으로 수정하고 저장하면 오류 해제 |

`01`~`07`, `09`, `10`에는 `Initial buffer ≈ 1 ms` 문구가 나타나지 않습니다.
선택적 scope/bin 힌트 설정을 켜지 않아도 acquisition 검사와 미적용 설정 경고가
동작합니다. 무한 반복도 compact 분석하므로 실제로 계속 실행하지 않습니다.
무한 반복 사례에는 RT 명령 큐의 전체 반복 분석을 생략했다는 **Analysis is
incomplete** 경고와 loop preview 안내가 별도로 표시됩니다. Acquisition 버퍼
판정과 구분해서 확인하세요. 이 coverage 경고만으로 initial buffer 문구가 뜨지는 않습니다.

각 사례는 독립 프로젝트입니다. 새 사례로 이동한 뒤 **Open Timeline Preview**를
다시 실행하세요. 미리보기를 여는 것만으로 기존 프로젝트에 없는 파일을 자동으로
추가하지 않습니다. 수정 사항은 **저장**할 때 재분석됩니다.

## 자동 확인

아래 명령으로 원본의 10개 결과를 확인할 수 있습니다. 의도적으로 수정한
예제는 먼저 원래대로 되돌려야 합니다.
오류 사례도 타임라인을 만들며 CLI 종료 코드 `2`를 반환하는 것이 정상입니다.

저장소 루트에서 현재 소스를 설치한 Python으로도 실행할 수 있습니다.

```powershell
python examples/local-changes-review/verify.py
```

판정 모델과 한계는 [acquisition 버퍼 설명](../../docs/acquisition-buffer.md)을
참고하세요. 확정 오류는 알려진 장비의 지속 불가능한 무한 부하에 한정합니다.
유한 burst가 문서화된 7회 허용치를 넘는 경우에는 실제 FIFO 상한을 모르므로
경고입니다.

별도로 로컬 변경에 포함된 실행 탐색 UI는 [Execution Flow 프로토타입](../../demo-execution-flow/README.md)입니다.
현재 VS Code 확장에 통합된 기능은 아닙니다. 해당 README의 실행 명령으로
실제 엔진에서 만든 별도 브라우저 데모를 열 수 있습니다.
