#!/bin/bash
# Double-clickable macOS launcher for the SIFAC Toolbox GUI.
#
# In Finder: double-click this file.
#
# "적절한 접근 권한이 없어 실행할 수 없습니다 / cannot be executed because you
# do not have appropriate access privileges" 가 뜨면, 압축을 풀 때 실행 권한이
# 사라진 경우입니다.  해결(택1):
#   1) 동봉한 .zip 을 Finder에서 더블클릭해 '보관 유틸리티(Archive Utility)'로
#      다시 풀면 권한이 보존됩니다.
#   2) 또는 이 파일이 있는 폴더에서 Terminal로 한 번만:  chmod +x run_mac.command
# 그리고 처음 실행 시 'unidentified developer' 경고가 나오면 → 우클릭 → 열기.
#
# It finds a Python with Tkinter, makes sure quickbms is available, and opens
# the GUI.

set -u
cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo " SIFAC Batch Extractor (QuickBMS) — macOS"
echo "==============================================="

# --- locate a python3 that has tkinter -------------------------------------
PY=""
for cand in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c "import tkinter" >/dev/null 2>&1; then
            PY="$cand"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo
    echo "[!] Tkinter가 있는 python3을 찾지 못했습니다."
    echo "    A Python 3 with Tkinter was not found."
    echo
    echo "    추천 (recommended):"
    echo "      1) https://www.python.org 에서 macOS 설치본을 받으세요 (Tk 포함)."
    echo "      2) 또는 Homebrew:  brew install python python-tk"
    echo
    echo "    설치 후 이 파일을 다시 더블클릭하세요."
    echo
    read -r -p "Press Enter to close..." _
    exit 1
fi
echo "[ok] python: $($PY --version 2>&1)  ($PY)"

# --- make sure quickbms exists ---------------------------------------------
if ! "$PY" sifac_extract.py --check >/dev/null 2>&1; then
    echo
    echo "[!] quickbms 실행 파일을 찾지 못했습니다. 빌드를 시도합니다…"
    echo "    quickbms was not found — trying to build it now."
    echo
    if [ -x "./build_quickbms_macos.sh" ] || [ -f "./build_quickbms_macos.sh" ]; then
        bash ./build_quickbms_macos.sh || {
            echo "[!] 자동 빌드 실패. 곧 열리는 GUI에서"
            echo "    [QuickBMS 빌드 (폴더 선택)…] 또는 [자동 다운로드+빌드] 버튼을 쓰세요."
        }
    fi
fi

"$PY" sifac_extract.py --check || true
echo
echo "GUI를 실행합니다… (launching GUI)"
"$PY" sifac_gui.py
