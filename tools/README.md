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

## 추출 후: ⭐ FBX로 바로 변환 (Noesis 불필요, MMD 아님)

풀린 `.bmarc`(모델/모션), `.bscam`(카메라), `.btx`(텍스처)를 **Noesis 없이**
곧바로 **FBX + PNG** 로 변환합니다. Noesis로 하나씩 여는 것보다 훨씬 빠르고,
`noesis-vmd`(MMD용 PMX/VMD)와 달리 **Blender·Unity·Unreal·Maya** 등에서
바로 쓰는 표준 FBX를 만듭니다.

```bash
# GUI: ② 변환 → FBX 탭 (run_mac.command / sifac_gui.py)
# CLI: 폴더 전체를 8개 동시 작업으로 FBX/PNG 변환
python3 sifac_convert.py /추출결과 /fbx출력 -j 8

# 모델/무대만 · 모션만 · 카메라만 · 텍스처만
python3 sifac_convert.py IN OUT --preset models
python3 sifac_convert.py IN OUT --preset animations
python3 sifac_convert.py IN OUT --preset cameras
python3 sifac_convert.py IN OUT --preset textures

# 무엇이 변환될지 미리 보기
python3 sifac_convert.py IN OUT --check
```

| 입력 | 출력 |
|------|------|
| 모델/무대 `.bmarc` | `.fbx` — 뼈대(스켈레톤)·스키닝·머티리얼·모프(표정) |
| 모션 `mot_*.bmarc` | `.fbx` — 같은 폴더의 모델을 리깅 + 모션을 take로 |
| 카메라 `.bscam` | `.fbx` — 위치·회전·FOV 애니메이션 카메라 |
| 텍스처 `.btx`/`texture.pac` | `.png` |

주요 옵션:

| 옵션 | 의미 |
|------|------|
| `-j, --jobs N` | 동시 실행 개수 (기본: CPU 코어 수) |
| `--preset all\|models\|animations\|cameras\|textures` | 변환 대상 |
| `--engine auto\|blender\|python` | FBX 생성 엔진 (아래 설명) |
| `--up-axis y\|z` | 출력 FBX의 Up 축. `y`(기본)는 **SIFAS·Maya·Unity와 동일한 Y-up** — 기존 SIFAS 모딩 툴 FBX와 같은 방향. `z`는 Z-up 파일. 둘 다 Blender에선 똑바로 섭니다 |
| `--scale F` | 전체 지오메트리/애니메이션 스케일 (기본 1.0) |
| `--anim-only` | 모션은 뼈+애니만 (메시 제외, 파일 작게) |
| `--bundle-motions` | 한 폴더의 모션들을 모델 FBX 하나에 take로 묶기 |
| `--models-dir DIR` | 모델이 모여있는 폴더. 라이브/모션이 모델과 다른 폴더에 있을 때, 캐릭터 이름(예: `03_kotori`)으로 자동 매칭 |
| `--model PATH` | 모든 모션을 리깅할 모델을 강제 지정 |
| `--no-morphs` / `--no-textures` | 모프 / 텍스처 디코드 생략 |
| `--include / --exclude GLOB` | 파일명 글롭으로 포함/제외 |
| `--skip-existing` | 이미 있는 결과는 건너뜀 |

> **⭐ FBX 엔진 (`--engine`):** 두 가지 엔진을 제공합니다. **둘 다 Blender에서
> 메시·스키닝·애니가 정상**으로 들어옵니다(아래 “Blender 호환” 참고).
>
> - **`python`**(권장·기본 후보) — 순수 파이썬 라이터. **무설치·빠르고**, Blender·
>   Unity·Maya 모두에서 바르게 deform/애니됩니다. 바인드 행렬·`BindPose`·단위(미터)를
>   표준대로 써서, 캐릭터가 **약 1.5 m로 똑바로(머리 위)** 들어옵니다.
> - **`blender`** — Blender 파이썬 모듈 `bpy`로 리그를 만들어 Blender가 직접 FBX를
>   내보냅니다. Blender의 본 방향(head/tail/roll)을 그대로 따르므로, Blender에서
>   **본 축까지 네이티브**로 맞추고 싶을 때 좋습니다. `pip install bpy` 필요(파이썬
>   버전 일치), 파일당 수십 초로 느립니다.
> - **`auto`**(기본) — `bpy`가 깔려 있으면 `blender`, 없으면 `python`.
>
> 대부분은 **무설치·고속의 `python`** 으로 충분합니다. Blender의 본 방향 컨벤션까지
> 완전히 맞춰야 하면 `pip install bpy` 후 `--engine blender`.
>
> > **방향(Up 축):** 두 엔진 모두 **기본 `--up-axis y`(Y-up)** 로, 기존 **SIFAS
> > 모딩 툴 FBX와 같은 축**으로 나옵니다 — Blender에선 똑바로 서고, Maya/Unity에서도
> > SIFAS와 동일하게 정렬됩니다. Blender 기본 Z-up 파일이 필요하면 `--up-axis z`.
> > (이전엔 `blender` 엔진이 옆으로 누워 SIFAS와 어긋났는데, 이제 바로잡혔습니다.)

> **모션 → 모델 짝짓기:** 모션 파일에는 본 이름만 있어 모델의 rest pose가
> 필요합니다. ① 같은 폴더의 모델 → ② 입력 트리 전체의 모델 → ③ `--models-dir`
> 폴더 순으로, **캐릭터 이름(`mot_03_kotori_…` ↔ `mod_03_kotori_…`)으로 자동
> 매칭**합니다. SIFAC처럼 `live/`(모션)와 모델이 분리돼 있으면 `--models-dir`로
> 모델 폴더를 가리키세요. 강제 지정은 `--model`.
>
> ```bash
> # 라이브(모션) 폴더를 변환하면서, 모델은 따로 추출해둔 폴더에서 매칭
> python3 sifac_convert.py /추출/live /fbx --preset animations --models-dir /추출/models
> ```

> **좌표계/UV:** Y-up이며 V축을 게임(좌상단)→FBX(좌하단)에 맞춰 뒤집습니다.
> 방향/스케일은 임포터에서도 조정할 수 있습니다.

> **BC7/BC6H 텍스처:** 표준 라이브러리만으로 raw·BC1~BC5를 디코드합니다.
> 고급 BC7/BC6H는 `pip install texture2ddecoder` 가 있으면 디코드하고, 없으면
> 해당 텍스처만 건너뜁니다(지오메트리/머티리얼은 정상 출력).

> **검증:** FBX 인코더·씬 빌더·텍스처 파이프라인은 자체 테스트로 검증합니다
> (`python3 tools/tests/test_convert.py`). 파일별 오류는 비치명적이라 나머지
> 변환은 계속됩니다. 실제 게임 파일에서 이상이 보이면 샘플을 알려주세요.

## ⭐ SIFAC 모션 → SIFAS 애니메이션 (Unity 없이 직접 리타깃)

SIFAC(PS4/아케이드) 모션 FBX를 **SIFAS(스쿠스타)용 `.anim` 클립**으로 곧바로
바꿉니다. **Unity의 Humanoid 리타깃을 거치지 않습니다.**

> **왜 Humanoid를 피하나요?** 보통은 모션을 Humanoid로 임포트해 Unity가
> 리타깃하게 두는데, Humanoid 리타깃은 **손실(lossy)** 입니다 — 머슬 공간으로
> 클램프하고, 본 길이를 정규화하며, Humanoid가 아닌 본은 전부 버립니다. 그래서
> 결과가 “반쪽짜리” 모션이 됩니다. 이 도구는 두 리그가 **같은 코어 스켈레톤**을
> 공유한다는 점을 이용해, 각 본이 **자기 rest에서 회전한 양을 월드 공간에서**
> SIFAS 리그로 옮깁니다(`dW = W_pose · W_rest⁻¹`). 월드 회전량은 두 리그가
> 본의 로컬 축을 다르게 잡아도 **꼬임(twist) 없이** 그대로 전달됩니다 — 로컬
> `matrix_basis`를 복사하면 축 차이만큼 비틀려 보이지만(=기괴한 움직임), 이
> 방식은 실제 SIFAS 메시에 적용해 자연스러운 포즈를 확인했습니다.

```bash
# Blender 파이썬 모듈이 필요합니다 (FBX 변환과 동일):  pip install bpy

# 한 개 변환
python3 sifac_anim_retarget.py \
    --sifac mot_06_maki_0510.fbx --out 0510.anim --name "0510 Daring"

# 폴더 전체 일괄 변환
python3 sifac_anim_retarget.py --batch ./sifac_motions --outdir ./anim_out
```

> **경로(중요):** 실제 SIFAS 게임 `.anim`은 본 경로를 **멤버 오브젝트 기준
> 상대경로**(`Reference/Move/Hips_Position/Hips/...`)로 바인딩합니다(애니메이터가
> 멤버에 붙어 있음). 그래서 **기본값은 접두어 없음**입니다. 멤버 이름을
> 앞에 붙이면(`ch0004_co0019_member/...`) 모델의 트랜스폼과 경로가 안 맞아
> **아무 반응이 없습니다**. 애니메이터가 멤버보다 위에 있는 특수한 셋업일
> 때만 `--member ch0004_co0019_member`로 접두어를 켜세요.

| 옵션 | 의미 |
|------|------|
| `--sifac FBX` / `--out ANIM` | 입력 SIFAC 모션 FBX / 출력 `.anim` |
| `--batch DIR` / `--outdir DIR` | 폴더 안 모든 `.fbx`를 일괄 변환 |
| `--member 이름` | (선택) 경로 접두어. 기본은 **없음**(게임 클립과 동일한 멤버 상대경로). 애니메이터가 멤버 위에 있을 때만 지정 |
| `--name 이름` | 클립 이름 (기본: 파일명) |
| `--prefab P` | 번들 스켈레톤 대신 특정 SIFAS 멤버 `.prefab`을 사용 |
| `--start N` / `--end N` | 변환할 프레임 구간 (원본 프레임) |
| `--step K` | 프레임 데시메이션 (예: `2` = 절반 키) |
| `--no-root-motion` | 무대 이동·상하 바운스(루트 모션) 생략 |
| `--no-twist-bones` | 롤/트위스트 본(`*ArmRoll`/`*ForeArmRoll`) 구동 생략 (아래 참고) |
| `--twist-strength X` | ArmRoll/ForeArmRoll을 **얼마나** 적용할지. `1.0`=게임값(기본), `0`=끔, `>1`=더 강하게 |
| `--smooth N` | (선택) N프레임 저역통과로 빠른 움직임(주로 팔)을 부드럽게. 기본 `0`(충실). `3`~`5` 권장 |
| `--format LIST` | 출력 형식(쉼표): `anim,fbx,glb,gltf,bvh` 중 선택. 기본 `anim`. 예: `--format anim,fbx,glb` |

> **GUI:** 도구상자(`sifac_gui.py`)의 **③ 리타깃 → SIFAS** 탭에서 위 옵션을
> 슬라이더·체크박스로 그대로 조절할 수 있습니다(ArmRoll 강도, 부드러움, 프레임
> 간격, 형식 선택, 일괄 폴더 처리). `bpy`는 ② 변환 탭의 원클릭 설치를 그대로
> 씁니다.

#### 여러 형식으로 내보내기 (`--format`)

`.anim`은 Unity 에디터에 바로 드롭하는 형식이고, **FBX / glTF(.glb) / BVH**는
SIFAS 리그에 모션을 **구워서**(bake) 내보내므로 Blender·Maya·웹 등 어떤 DCC에서나
열립니다. 한 번에 여러 개도 가능합니다:

```bash
python3 sifac_anim_retarget.py --sifac mot_06_maki_0510.fbx \
    --out 0510.anim --twist-strength 0.8 --smooth 3 \
    --format anim,fbx,glb,bvh
```

- **anim** — Unity 제네릭 AnimationClip(에디터 텍스트). Unity 프로젝트에 바로.
- **fbx** — 뼈대+애니. 표준. 어떤 툴에서나.
- **glb / gltf** — 범용·웹 3D 표준(`glb`=단일 바이너리).
- **bvh** — 모캡(뼈대+모션). 다른 리그로 재타깃하기 좋음.

> 루트/무대 이동은 FBX·glTF에서는 오브젝트(아마튜어) 트랜스폼 애니로, BVH에서는
> 루트 본 채널로 함께 구워집니다. `--no-root-motion`으로 끌 수 있습니다.

**무엇이 들어가나:** 두 리그가 공유하는 **코어 본 60개의 회전 곡선** +
**`Hips_Position` 위치 곡선**(루트/무대 모션 — 실제 게임 클립과 같은 노드) +
**롤/트위스트 본 4개**(아래 “트위스트 본” 참고).
출력은 표준 제네릭 `.anim`(쿼터니언 `m_RotationCurves`, CRC32 경로 해시의
`ClipBindingConstant`, 60fps 메타)이라 Unity에 그대로 드롭하면 됩니다. 경로·
바인딩·해시·세트팅 구조를 실제 SIFAS `.anim`과 맞춰 검증했습니다(`attribute`
1=위치/2=회전, `typeID 4`=Transform). 그 외 SIFAS에만 있는 본(`Spine2` 등)은
부모를 따라가되 부모 기준 rest를 유지합니다. `m_EulerCurves`/`m_EditorCurves`는
에디터 표시용이라 비워두며 런타임 재생에는 영향이 없습니다.

#### 트위스트 본 — 어깨·팔 살 접힘(파임) 방지

SIFAS 리그에는 SIFAC에 없는 **롤 본**(`Left/RightArmRoll`,
`Left/RightForeArmRoll`)이 있습니다. 이 본들을 rest로 두면 팔/손목이 크게
비틀릴 때 어깨·팔꿈치 살이 **사탕 포장지처럼 접히고 안쪽으로 파입니다**. 실제
SIFAS 게임 클립은 이 본들을 구동해 비틀림을 분산시킵니다. 이 도구는 그 게임
리그 공식을 **그대로** 재현합니다 — 출하된 클립(`ch0202`)에서 측정한 값:
`ArmRoll = −½·(Arm의 X축 비틀림)`, `ForeArmRoll = +½·(Hand의 X축 비틀림)`(교과서
적 절반 비틀림). 측정 결과 상완 어깨쪽 단면이 강한 비틀림 프레임에서 약 **20%
더 채워져**(덜 파임) 보입니다. 끄려면 `--no-twist-bones`.

> **부드러움(smoothness)에 대해:** 직접 리타깃은 **원본의 빠른 팔 동작을 그대로**
> 살립니다. Humanoid 리타깃 결과보다 “덜 부드럽게” 느껴질 수 있는데, 이는
> Humanoid가 머슬 공간으로 **빠른 팔 움직임을 클램프·평활화(손실)** 하기 때문이고,
> 우리 결과의 그 동작은 **지터(노이즈)가 아니라 실제 모션**입니다(스파이크의
> 99%가 여러 프레임에 걸친 지속 동작). 더 부드러운(Humanoid 같은) 느낌을 원하면
> `--smooth 3` 정도로 살짝 저역통과를 거세요 — 충실도를 조금 내주고 팔을 부드럽게
> 만듭니다.

> **검증:** 좌표 변환은 라운드트립으로 검증됩니다 — 리그를 다시 만들어 되읽으면
> 프리팹 자신의 로컬 쿼터니언을 **~0.04°** 이내로 복원하고, 추출한 로컬 회전을
> Unity 계층으로 다시 누적하면 의도한 월드 포즈를 **~0.1°** 이내로 재현합니다.
> 좌우 본도 정확히 일치합니다(미러 없음). `--member`에 들어가는 대상 캐릭터의
> 멤버 오브젝트 이름만 맞추면 됩니다. 코어 스켈레톤은 모든 SIFAS 아이돌이
> 공유하므로 번들 스켈레톤으로 충분하고, 특정 코스튬에 정확히 맞추려면
> `--prefab`로 그 캐릭터의 멤버 프리팹을 가리키세요.

> **Direct SIFAC→SIFAS animation retarget (English):** converts a SIFAC motion
> FBX straight to a SIFAS generic `.anim`, **bypassing Unity's lossy Humanoid
> retargeting**. It copies each shared bone's rest-relative local rotation from
> the SIFAC rig onto the SIFAS rig (both share the core skeleton), then reads it
> back in Unity space and writes rotation curves + a Hips position curve with a
> correct `ClipBindingConstant` (CRC32 path hashes). Needs `pip install bpy`.
> The coordinate maths is validated by a rest round-trip (~0.04°) and a
> hierarchy replay (~0.1°), with left/right bones matching exactly. It also
> drives the SIFAS **twist/roll bones** (`*ArmRoll`/`*ForeArmRoll`) the way the
> shipped game clips do — `ArmRoll = −½·twist(Arm)`, `ForeArmRoll = +½·twist(Hand)`
> about the limb's X axis — so the shoulder/elbow skin doesn't pinch on hard
> twists (disable with `--no-twist-bones`). Fast arm motion is kept faithfully;
> `--smooth N` is an optional low-pass if you prefer the softer Humanoid feel.

### 팔만 자연스럽게: 충실한 본체 + Humanoid 팔 병합 (`sifac_anim_merge.py`)

직접 리타깃은 **원본 SIFAC 모션에 충실**합니다 — 몸통·다리·손가락·루트 모션엔
이상적입니다. 그런데 **팔**은 SIFAC가 가리키는 그대로 SIFAS 리그에 올리면
어색해 보일 수 있습니다. SIFAS 캐릭터는 자기 리그의 자연스러운 가동범위로 팔을
들어야 자연스러운데, 그게 바로 Unity Humanoid 리타깃이 만들어주는 결과입니다.

이 도구는 **둘의 장점만** 합칩니다: 충실한 클립에서 **팔 그룹만** Humanoid
클립의 곡선으로 바꾸고, 나머지(몸통·다리·**손가락**·루트)는 전부 충실한 쪽을
유지합니다.

```bash
# 1) 충실한 .anim (sifac_anim_retarget.py 결과)
# 2) 자연스러운 .anim (같은 모션을 Unity Humanoid로 리타깃한 것)
# 3) 병합  →  자연스러운 팔 + 충실한 손가락/몸/다리
python3 sifac_anim_merge.py faithful.anim natural.anim out.anim
```

- 두 클립은 **같은 모션, 같은 SIFAS 스켈레톤(멤버 상대경로), 시간 정렬**이어야 합니다.
- 기본으로 Humanoid에서 가져오는 본: `LeftShoulder RightShoulder Left/RightArm
  Left/RightForeArm Left/RightHand Neck Head` (+`*Roll`). `--natural-bones` /
  `--also-natural`로 조절. 나머지·**손가락은 충실한 쪽** 유지(Humanoid가 손가락을
  뭉개는 걸 보완).
- **48° 방향 차이는 문제 없음**: 로컬 회전은 전역 회전에 불변이라, 바꿔 끼운
  팔이 자동으로 본체의 방향 프레임을 따릅니다.

> **English:** `sifac_anim_merge.py` combines a *faithful* clip (the direct
> retarget) with a *natural* clip (your Unity Humanoid retarget): it keeps every
> curve from the faithful one except the **arm group**, which it swaps in from
> the Humanoid clip. Result = Humanoid-natural arms that fit the SIFAS rig +
> faithful body, legs, **fingers** and root motion. The two clips must be the
> same motion on the same SIFAS skeleton, time-aligned. Local rotations are
> global-rotation-invariant, so the swapped arms inherit the body's facing
> automatically.

### Unity 번들로 주입 (`sifac_anim_to_bundle.py`)

`.anim`은 Unity **에디터** 텍스트 자산입니다. 게임은 **AssetBundle**(바이너리
`UnityFS`)을 읽으므로, 클립을 게임에 넣으려면 번들 안에 들어가야 합니다. Unity
없이 번들을 **처음부터** 만드는 건 불안정하므로, 이 도구는 게임에서 추출한 **실제
번들을 템플릿**으로 받아 그 안의 `AnimationClip`을 우리 클립으로 **바꿔치기**한 뒤
다시 묶습니다 (`pip install UnityPy`, Unity·bpy 불필요).

```bash
# 1) (선택) 번들 내부 형식 확인
python3 sifac_anim_to_bundle.py --inspect mvjubr_0.unity --dump-tree

# 2) 주입: 템플릿 번들 + 우리 .anim → 새 번들
python3 sifac_anim_to_bundle.py --template mvjubr_0.unity \
    --anim 0510.anim --out 0510_inj.unity
```

> **형식(확인됨):** SIFAS 번들은 **Unity 2018.4**, 클립은 **제네릭**이라 모션이
> `m_MuscleClip`(런타임)에 들어 있고 에디터 커브(`m_RotationCurves`)는 **비어
> 있습니다**. 바인딩(`genericBindings`)은 `path`가 멤버 상대경로의 **CRC32 해시**,
> `attribute` 1=위치·2=회전(쿼터니언)·3=스케일·4=오일러, `typeID` 4(Transform).
> 이 도구는 우리 클립을 **DenseClip**(모든 커브를 균일 샘플링) 하나로 베이크하고
> Streamed/Constant를 비워, 커브 인덱스 순서 = 바인딩 순서가 되게 합니다.
> **클립 이름은 기본적으로 유지**하므로(예: `ch0202_so2003`) 게임이 그 자리에서
> 우리 모션을 로드합니다. 나머지(Avatar·Animator·본 계층·다른 에셋)는 그대로.

> **검증:** 헤드리스로 — 써 넣은 DenseClip을 다시 디코드하면 원본 `.anim` 값과
> **~1e-7**까지 일치하고, 번들 repack→reload도 정상입니다. **게임 내 실제 재생만**
> Unity가 필요하니 마지막엔 게임에서 확인하세요. (이름을 바꾸려면 `--clip-name`,
> 샘플레이트는 `--fps`.)

> **English:** `.anim` is a Unity *editor* asset; the game loads binary
> AssetBundles. `sifac_anim_to_bundle.py` takes a **template bundle** (a real
> SIFAS animation bundle you extracted) and bakes the retargeted motion into its
> runtime clip, then repacks — Unity-free, via `UnityPy`. SIFAS clips
> (Unity 2018.4, generic) keep motion in `m_MuscleClip`, so the tool writes a
> single `DenseClip` with matching `genericBindings` (CRC32 path hash, attribute
> 1=pos/2=rot, typeID 4) and keeps the clip name so the game loads your motion in
> its place. The written clip decodes back to the source `.anim` to ~1e-7 and the
> bundle re-loads cleanly; only in-game playback needs Unity, so test it there.

### 게임에 넣기 — 설치 (`sifac_bundle_install.py`)

수정한 번들을 그냥 덮어써도 게임은 안 받습니다. SIFAS는 KLab의 **Octo** 에셋
시스템을 써서, DB(매니페스트)에 각 팩의 **size·md5**(·crc)가 들어 있고, 파일이
DB와 다르면 **손상**으로 보고 원본을 다시 받습니다. 그래서 설치는 두 단계:

1. **배치** — 새 번들을 게임이 두는 위치에 팩 이름(예: `mvjubr_0`)으로 둡니다.
2. **DB 갱신** — Octo DB의 그 팩 행을 새 size·md5(·crc)로 고쳐, 무결성 검사를
   통과시킵니다.

이 도구는 스키마 없이도 확실한 부분을 해 줍니다:

```bash
# 1) 새 번들의 무결성 값 — DB에 써 넣을 size·md5·crc (+ 내부 CAB 이름)
python3 sifac_bundle_install.py --info mvjubr_0__0510Daring.unity

# 2) Octo DB(SQLite)에서 팩 행 찾기 — 어떤 테이블/열에 들어있는지
python3 sifac_bundle_install.py --scan-db octo.db --find mvjubr_0

# 3) 행 갱신 (스키마 확인 후; 기본 dry-run, 실제 쓰기는 --apply)
python3 sifac_bundle_install.py --patch-db octo.db --bundle mvjubr_0__0510Daring.unity \
    --table assetbundle --name-col name --name mvjubr_0 \
    --size-col size --md5-col md5 --crc-col crc --apply
```

> **필요:** 정확한 패치를 켜려면 **당신 클라이언트의 Octo DB(매니페스트) 샘플**이
> 필요합니다(번들 때와 같은 방식). `--scan-db` 결과를 주시면 테이블·열을 고정하고
> 읽기-검증까지 붙입니다. Octo DB가 암호화/난독화돼 있으면 그 복호 단계도 추가합니다.

> **English:** A modded bundle won't load until the game's **Octo** database
> agrees with it: Octo stores each pack's size/md5(/crc), and a mismatch makes
> the client re-download the original. `sifac_bundle_install.py --info` prints
> the exact size/md5/crc (and internal CAB name) to write; `--scan-db` finds the
> pack's row in the SQLite Octo db; `--patch-db` updates it (dry-run unless
> `--apply`). Share a sample Octo db and the patch step is pinned to your
> client's schema and validated by reading the row back.

### (참고) 옛 방식 — Noesis 에서 열기

풀린 `.bmarc`/`.btx`는 이 저장소의 Noesis 플러그인
(`plugins/python/fmt_Blade_bmarc.py`)으로도 열 수 있습니다. 모션(`mot_*`)은
같은 폴더의 모델과 함께 로드해야 본에 적용됩니다. VMD(MMD) 내보내기에는 vmd
모듈이 필요합니다: <https://github.com/h-kidd/noesis-vmd>

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
