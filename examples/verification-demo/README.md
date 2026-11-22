# VS Code에서 검증 결과 따라가기

[데모 워크스페이스](verification.code-workspace)를 열고 아래 파일을 선택하세요. 소스에 검증 결과를 표시하려면 상태 표시줄의 **Z3 Auto: Off**를 눌러 자동 검증을 켜세요. 화면의 **Proved**는 표시된 범위에서 증명됨, **Counterexample**은 재현된 반례 있음, **Unknown**은 주어진 모델이나 예산으로 결론을 내릴 수 없다는 뜻입니다.

| 열 파일 | 확인할 동작 |
| --- | --- |
| [queue.q1asm](queue.q1asm) | `.DEF N 20` 위의 결과를 클릭하세요. 기본 RT 큐 설정에서 `N=1..37`은 안전하고, `N=38`의 타임라인은 큐가 처음 비는 1188 ns를 선택합니다. **Sweep**의 **Sequencer**에서 `queue`를 고른 뒤 **Verify saved program**을 눌러도 같은 경계가 나옵니다. |
| [assertions.q1asm](assertions.q1asm) | `@readout` 진입 시각은 `N=1..513` 전체에서 20000 ns입니다. `R2 < 1024` 위의 반례를 클릭하면 `N=513`의 타임라인과 R2의 쓰기 기록이 열립니다. |
| [feedback.q1asm](feedback.q1asm) | **Q1Lens: Open Timeline Preview**에서 5행의 `jnz @active` 분기 메뉴를 열고 **Compare both**를 선택하세요. 소스 9행의 `jz`에 마우스를 올리면, 같은 비트를 검사한 5행의 선택 때문에 두 번째 점프가 도달 불가라는 설명을 볼 수 있습니다. |
| [alignment/drive.q1asm](alignment/drive.q1asm) | 타임라인 도구 모음에서 **Verify**(Verify alignment)를 누르세요. 네 입력 비트의 16가지 조합에서 두 시퀀서가 `@readout`과 종료 시점에 정렬됩니다. |
| [alignment/late/drive.q1asm](alignment/late/drive.q1asm) | 같은 버튼으로 실패 사례를 확인하세요. 홀수 개의 비트가 켜지면 seq3가 20 ns 늦어집니다. 결과 행의 **Open**(Open counterexample timeline)이 두 시퀀서를 함께 보여줍니다. |
| [before.q1asm](before.q1asm) | **Q1Lens: Compare RT Traces Across Inputs**로 [optimized.q1asm](optimized.q1asm)을 고르면 8개 입력이 모두 통과합니다. [changed.q1asm](changed.q1asm)을 고르면 `N=7`에서 wait 종료가 4 ns 달라집니다. |

CLI에서도 같은 저장된 파일을 검증할 수 있습니다. 데모 디렉터리에서 실행하세요.

```sh
q1lens verify queue.q1asm --json
q1lens verify assertions.q1asm --json --include-replay
q1lens verify alignment/q1timeline.yml --alignment
q1lens verify alignment/late/q1timeline.yml --alignment
q1lens equiv before.q1asm optimized.q1asm
q1lens equiv before.q1asm changed.q1asm --json
```
