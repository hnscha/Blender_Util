# Blender_Util

Blender 용 유틸리티 애드온 모음.

## 폴더 규칙

- 저장소 최상위에 **애드온 하나당 폴더 하나**를 둔다.
- 각 폴더는 Blender 4.2+ 확장(Extension) 형식이다: `blender_manifest.toml` + `__init__.py`.
- 이 저장소 폴더를 Blender 의 로컬 확장 저장소로 등록하면(Preferences > Get Extensions > Repositories > Add Local Repository) 폴더 안의 애드온이 바로 목록에 나타난다.
- `_backup_*`, `__pycache__` 는 커밋하지 않는다 (`.gitignore`).

## 릴리스와 설치

- GitHub 저장소의 Actions 탭 > "Release add-on" > Run workflow 에서 애드온 폴더 이름(예: `dcc_navigation`)을 넣고 실행하면, 그 폴더 `blender_manifest.toml` 의 `version` 으로 `<폴더>-v<버전>` 태그를 만들고 zip 을 Releases 에 올린다.
- 같은 형식의 태그를 직접 푸시해도 된다. 예: `git tag dcc_navigation-v1.0.0 && git push origin dcc_navigation-v1.0.0`. 이때 태그 버전이 manifest `version` 과 다르면 릴리스가 실패한다.
- 새 버전을 낼 때는 manifest 의 `version` 을 먼저 올린다.
- 다른 PC 에서는 Releases 에서 zip 을 받아 Blender 창에 끌어다 놓거나, Preferences > Add-ons > Install from Disk 로 설치한다 (Blender 4.2+).

## 애드온 목록

| 폴더 | 이름 | 설명 |
|---|---|---|
| `dcc_navigation/` | DCC Navigation | 뷰포트 조작 모드 전환 (Blender 기본 / Maya·Unreal / 3ds Max). QWER 툴, 1/2 World/Local, 변환 중 기즈모 유지 (Object/Pose/Edit 모드). 3D 뷰포트 N 패널 "DCC Nav" 탭. |
| `strict_lock/` | Strict Lock | 자물쇠 걸린 변환 채널을 정말 못 바꾸게 함 — 뷰포트 드래그, 숫자 칸 끌기·입력, 커브 에디터 편집, 잠긴 채널 새 키 모두 되돌림. 3D 뷰포트 N 패널 "Lock" 탭. |
| `anim_studio/` | Anim Studio | 애니메이터 편의 도구(개발 중, 0단계). 베이크·스냅샷·임시 객체·미러 공통 엔진 + 단축키 지정용 버튼 묶음. 3D 뷰포트 N 패널 "Anim Studio" 탭. 진행 기록은 `anim_studio/docs/진행상황_하네스.md`. |
