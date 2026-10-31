# Blender_Util

Blender 용 유틸리티 애드온 모음.

## 폴더 규칙

- 저장소 최상위에 **애드온 하나당 폴더 하나**를 둔다.
- 각 폴더는 Blender 4.2+ 확장(Extension) 형식이다: `blender_manifest.toml` + `__init__.py`.
- 이 저장소 폴더를 Blender 의 로컬 확장 저장소로 등록하면(Preferences > Get Extensions > Repositories > Add Local Repository) 폴더 안의 애드온이 바로 목록에 나타난다.
- `_backup_*`, `__pycache__` 는 커밋하지 않는다 (`.gitignore`).

## 릴리스와 설치

- `<폴더>-v<버전>` 형식의 태그를 푸시하면 GitHub Actions 가 그 애드온 폴더를 zip 으로 묶어 Releases 에 올린다. 예: `git tag dcc_navigation-v1.0.0 && git push origin dcc_navigation-v1.0.0`
- 태그의 버전은 그 폴더 `blender_manifest.toml` 의 `version` 과 같아야 한다. 다르면 릴리스가 실패한다.
- 다른 PC 에서는 Releases 에서 zip 을 받아 Blender 창에 끌어다 놓거나, Preferences > Add-ons > Install from Disk 로 설치한다 (Blender 4.2+).

## 애드온 목록

| 폴더 | 이름 | 설명 |
|---|---|---|
| `dcc_navigation/` | DCC Navigation | 뷰포트 조작 모드 전환 (Blender 기본 / Maya·Unreal / 3ds Max). QWER 툴, 1/2 World/Local, 변환 중 기즈모 유지. 3D 뷰포트 N 패널 "DCC Nav" 탭. |
