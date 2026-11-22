# Strict Lock 성능 수정 검증

`strict_lock_regression.py`는 별도 background Blender에서만 실행한다. 라이브 세션이나 작업 파일에는 저장하지 않는다.

- `-- regression`: 수정 전 Git blob과 현재 소스를 같은 26개 의미 검사로 비교한다.
- `-- benchmark baseline` / `-- benchmark optimized`: 별도 프로세스에서 저장된 변환 리그 사본을 열고 콜백/전체 갱신 시간을 측정한다. 성능 측정끼리는 동시에 실행하지 않는다.
- `--baseline PATH`로 기존 소스를 직접 지정할 수도 있다. 기본 기준은 Git blob `597b37a01f15893ce2e247b498ea3f0f9a1343f8`이다.

예: `blender --background --factory-startup --python "tests/strict_lock_regression.py" -- regression`

`strict_lock_bulk_probe.py`는 foreach_get 일괄 조회를 평가한 미채택 실험이다. 기존/실험 각각 26개 의미 검사는 통과했으나 콜백이 약 5.2→10.1ms로 느려져 제품 코드에 적용하지 않았다. 결과 JSON은 기각 이유를 남기기 위한 기록이다.

26개 검사는 값·키·핸들·삭제된 커브 복원, unlock/relock, driver 추가/삭제, 회전 모드, NLA, 소유자 재생성 및 Undo를 포함한다. 이 테스트가 UI 입력부터 화면 출력까지 지연을 측정하는 것은 아니다.
