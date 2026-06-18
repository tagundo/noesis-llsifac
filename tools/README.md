# SIFAC Batch Extractor (QuickBMS GUI + 배치 도구)

SIFAC (*Love Live! School idol festival After school aCtivity*, PS4/Arcade)
의 **모델링·텍스처·라이브(모션)** 데이터를 QuickBMS로 한꺼번에, 그리고
**여러 개를 동시에** 추출하기 위한 도구입니다. 맥북에서 쉽게 쓰도록
더블클릭 실행 파일과 GUI를 제공합니다.

> Batch + GUI front-end for QuickBMS that extracts SIFAC models / textures /
> live(motion) data **in parallel**, with a Mac‑friendly double‑click launcher.

---

## 왜 더 빠른가 (Why it's faster)

QuickBMS 자체는 한 번에 파일 하나씩만 처리합니다. 기존 방식은 파일을
하나하나 손으로 여는 것이라 수천 개를 풀려면 매우 오래 걸립니다.
이 도구는 폴더 전체를 한 번에 스캔하고 **`--jobs` 개수만큼 QuickBMS를
동시에 실행**하며, 2단계(압축해제 → 아카이브 추출)를 자동으로 연결합니다.

```
1단계  .cmp (cmp\0, LZMA)  --LoveLive_CMP.bms-->  ARC 아카이브
2단계  ARC (ARC\0)         --LoveLive_PAC.bms-->  .bmarc(모델/모션) .btx(텍스처) 등
```

압축을 풀면 파일명이 그대로(`live_0001.cmp`) 남기 때문에, 이 도구는
**확장자가 아니라 파일 매직(`ARC\0`)으로** 아카이브를 찾아 2단계로
넘깁니다.

---

## ⭐ 가장 쉬운 길: QuickBMS 없이 (네이티브 · 파이썬)

맥에서 QuickBMS를 컴파일하는 게 번거롭다면, **컴파일이 아예 필요 없는**
순수 파이썬 추출기를 쓰세요. SIFAC의 두 포맷(`.cmp` LZMA, `ARC` 아카이브)을
QuickBMS와 동일하게 처리하도록 `LoveLive_*.bms` 스크립트와 QuickBMS 소스를
기준으로 구현했고, 왕복(round-trip) 테스트로 검증했습니다.

- **GUI:** 옵션의 **`QuickBMS 없이 추출 (네이티브 · 파이썬)`** 체크박스를 켜세요.
  QuickBMS가 안 잡히면 자동으로 켜집니다.
- **CLI:** `--native` 플래그.
  ```bash
  python3 sifac_extract.py /게임데이터 /출력 -j 8 --native --preset models
  ```

> 표준 라이브러리만 사용(`lzma` 포함)하므로 추가 설치가 없습니다. 매우 큰
> 아카이브는 통째로 메모리에 올리므로, 그런 경우엔 스트리밍 처리하는
> QuickBMS 쪽이 더 안전합니다. 실제 SIFAC 파일에서 문제가 보이면 샘플을
> 알려주시면 맞춰 드립니다.

## "QuickBMS 소스 경로"가 뭔가요? (What is the QuickBMS source path?)

= QuickBMS **저장소를 받아둔 폴더**입니다. 그 안에 `src/quickbms.c` 와
`Makefile` 이 들어 있습니다.

맥에는 Windows의 `quickbms.exe` 같은 **바로 쓰는 실행 파일이 없기 때문에**,
이 소스를 한 번 컴파일해서 `quickbms` 실행 파일을 만들어야 합니다. 이
도구가 그 컴파일과 설치를 대신 해줍니다(결과물은 `tools/bin/` 에 들어가
자동 인식). 즉, "소스 경로"는 **이미 받아둔 quickbms 저장소 폴더**를
가리키며, 그게 `noesis-llsifac` 옆(형제 폴더)에 있으면 **경로를 입력할
필요도 없습니다.**

## 빠른 시작 — 맥북 (Quick start on macOS) · 터미널 없이

1. **Python 준비** — Tk가 포함된 Python 3.
   - 권장: <https://www.python.org> 의 macOS 설치본 (Tk 포함), 또는
   - Homebrew: `brew install python python-tk`
2. **GUI 실행** — Finder에서 `run_mac.command` **더블클릭**.
   - 처음엔 macOS가 막을 수 있습니다 → 우클릭 → **열기**.
3. **QuickBMS 준비** — GUI 안에서 버튼으로 끝냅니다. (quickbms 줄이
   `NOT FOUND` 일 때만)
   - **[QuickBMS 빌드 (폴더 선택)…]** → 받아둔 QuickBMS 폴더를 고르면 자동 빌드, 또는
   - **[자동 다운로드+빌드]** → 인터넷에서 받아 빌드(네트워크 필요)
   - 끝나면 quickbms 경로가 **자동으로 채워집니다**.
   - QuickBMS 폴더가 형제 위치에 있으면 이 단계도 생략(자동 인식).
4. **추출** — 입력 폴더(게임 데이터)·출력 폴더를 고르고 ▶ **추출 시작**.

> 터미널을 선호하면 동일하게 빌드할 수 있습니다:
> ```bash
> cd noesis-llsifac/tools
> ./build_quickbms_macos.sh                 # 형제 폴더 자동 탐색
> ./build_quickbms_macos.sh /받아둔/quickbms # 저장소 폴더 직접 지정
> ./build_quickbms_macos.sh --download       # 자동 다운로드 후 빌드
> ```
> 맥에서 32비트(`-m32`) 빌드는 더 이상 동작하지 않으므로, 스크립트가
> 자동으로 64비트(SSL 비활성) 빌드로 재시도합니다. SSL은 QuickBMS의 자동
> 업데이트에만 쓰이고 추출에는 영향이 없습니다.

---

## GUI 사용법

| 항목 | 설명 |
|------|------|
| 입력 폴더 (Input) | 추출할 SIFAC 파일들이 있는 폴더 (하위 폴더 포함 재귀 스캔) |
| 출력 폴더 (Output) | 결과가 저장될 폴더 |
| quickbms 경로 | 자동 감지됨. 없으면 빌드하거나 **찾기…** 로 직접 지정 |
| 동시 작업 수 | 병렬 실행 개수. CPU 코어 수 정도가 적당 (속도 핵심) |
| 추출 대상 | 전체 / 모델링 / 라이브·모션 / 텍스처 |
| .cmp 압축해제 / .arc 추출 | 단계 on/off |
| 기존 파일 건너뛰기 | 이미 있는 파일은 덮어쓰지 않음 |

진행률 막대와 로그 창에서 실시간 상태를 볼 수 있고, **중지** 버튼으로
실행 중인 QuickBMS 프로세스를 즉시 종료할 수 있습니다.

---

## 명령줄 (CLI) — 자동화/대량 처리

GUI 없이도 동일한 엔진을 씁니다:

```bash
# 폴더 전체를 8개 동시 작업으로 추출
python3 sifac_extract.py /게임데이터 /출력 -j 8

# 모델링만
python3 sifac_extract.py IN OUT --preset models

# 라이브/모션만, 특정 파일만 (파일명 글롭)
python3 sifac_extract.py IN OUT --preset live --include "*live*"

# 압축해제만 / 아카이브 추출만
python3 sifac_extract.py IN OUT --stage cmp
python3 sifac_extract.py IN OUT --stage arc

# 무엇이 감지됐는지만 확인
python3 sifac_extract.py --check

# 실제 실행 없이 명령만 미리보기
python3 sifac_extract.py IN OUT --dry-run
```

주요 옵션:

| 옵션 | 의미 |
|------|------|
| `-j, --jobs N` | 동시 실행 개수 (기본: CPU 코어 수) |
| `--stage all\|cmp\|arc` | 전체 / 압축해제만 / 추출만 |
| `--preset all\|models\|live\|textures` | 아카이브 내부 콘텐츠 필터 프리셋 |
| `-f, --content-filter` | QuickBMS `-f` 필터를 직접 지정 (프리셋보다 우선) |
| `--include / --exclude GLOB` | 입력 파일명 글롭으로 포함/제외 (반복 가능) |
| `--skip-existing` | 덮어쓰지 않고 건너뜀 (`-k`) |
| `--quickbms PATH` / `--scripts DIR` | 경로 수동 지정 |
| `-v, --verbose` | QuickBMS 출력까지 자세히 |

### 출력 구조

```
출력/
├─ 01_decompressed/…   # .cmp 압축해제 결과 (입력 폴더 구조를 그대로 미러링)
└─ 02_extracted/<경로>/<아카이브이름>/…   # ARC 에서 풀린 .bmarc / .btx / …
```

아카이브당 폴더는 **딱 한 단계**만 만듭니다. 그리고 폴더 이름이 연속으로
겹치면(예: `live_0001/live_0001`) 자동으로 **하나로 합쳐서** 깊은 중복
폴더가 생기지 않습니다.

- GUI: **`중복 폴더 이름 합치기`** 체크박스 (기본 켜짐)
- CLI: 기본 켜짐. 끄려면 `--no-collapse`

> input → output1 → output2 처럼 **여러 번 추출**할 때 같은 이름의 폴더가
> 계속 겹쳐 깊어지던 문제가 이 기능으로 해결됩니다.

---

## 추출 후 Noesis 에서 열기

풀린 `.bmarc`(모델/모션), `.btx`(텍스처) 등은 이 저장소의 Noesis 플러그인
(`plugins/python/fmt_Blade_bmarc.py`)으로 열 수 있습니다.
모션(`mot_*`)은 같은 폴더의 모델과 함께 로드해야 본에 적용됩니다.
VMD 내보내기에는 vmd 모듈이 필요합니다:
<https://github.com/h-kidd/noesis-vmd>

---

## 콘텐츠 프리셋에 대해

`--preset` 은 QuickBMS `-f` 필터로 **아카이브 내부에서** 어떤 파일을
뽑을지 고릅니다. SIFAC은 모션을 `mot_*` 로, mdl/tex/mot 를
`.bmarc/.btx/.bmarc` 로 만듭니다. 기본 프리셋은 보편적인 값이며,
필요하면 `sifac_extract.py` 상단 `PRESET_CONTENT_FILTERS` 에서 자유롭게
수정하거나 `-f` 로 직접 지정하세요.

| 프리셋 | 필터 |
|--------|------|
| `all` | (필터 없음) |
| `models` | `{}.bmarc;{}.btx;{}.pac;{}.shp;{}.shg;!{}mot_{}` |
| `live` | `{}mot_{};{}.bscam;{}.efx;{}.efxa` |
| `textures` | `{}.btx;{}.pac` |

---

## 문제 해결 (Troubleshooting)

- **`Tkinter is not available`** — Tk 포함 Python을 설치하세요
  (python.org 설치본 또는 `brew install python-tk`).
- **`quickbms ... NOT FOUND`** — `build_quickbms_macos.sh` 로 빌드하거나
  GUI의 **찾기…** 로 직접 지정. `--check` 로 감지 상태 확인.
- **빌드 실패** — Xcode CLT 설치(`xcode-select --install`) 후 재시도.
  스크립트가 64비트(SSL 비활성)로 자동 재시도합니다.
- **일부 파일 실패** — 손상/암호화/비표준 파일일 수 있습니다.
  `-v` 로 자세한 로그를 확인하세요. 나머지 파일 처리는 계속됩니다.

---

*요구사항: Python 3.8+ (표준 라이브러리만 사용, 추가 설치 불필요).
Windows에서는 `run_windows.bat`, 그 외에는 `python3 sifac_gui.py`.*
