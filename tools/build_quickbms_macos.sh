#!/bin/bash
# Build QuickBMS for the SIFAC Batch Extractor.
#
# "QuickBMS 소스 경로"란?  = QuickBMS 저장소를 내려받아 둔 폴더입니다.
#   그 안에는 src/quickbms.c 와 Makefile 이 들어 있습니다. 이 스크립트가 그
#   소스를 컴파일해서 quickbms 실행 파일을 만들어 tools/bin/ 에 넣어줍니다.
#
# 사용법 (Usage):
#   ./build_quickbms_macos.sh                      # 근처의 QuickBMS 소스를 자동 탐색
#   ./build_quickbms_macos.sh /path/to/quickbms    # 저장소 루트 (src/quickbms.c 보유)
#   ./build_quickbms_macos.sh /path/to/quickbms/src# src 폴더 자체
#   ./build_quickbms_macos.sh --download           # git clone 후 빌드 (네트워크 필요)
#   QUICKBMS_SRC=/path ./build_quickbms_macos.sh
set -u

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$TOOLS_DIR/bin"
DL_DIR="$TOOLS_DIR/.quickbms_src"
DL_URL="${QUICKBMS_URL:-https://github.com/tagundo/quickbms}"

# Echo the directory that actually contains quickbms.c (the src dir), given
# either a repo root or the src dir itself. Returns non-zero if not found.
normalize_src() {
    local p="$1"
    [ -z "$p" ] && return 1
    [ -f "$p/quickbms.c" ] && { echo "$p"; return 0; }
    [ -f "$p/src/quickbms.c" ] && { echo "$p/src"; return 0; }
    return 1
}

clone_quickbms() {
    # NOTE: only the resolved source path may go to stdout (it is captured by
    # the caller via $(...)). All status text goes to stderr.
    command -v git >/dev/null 2>&1 || { echo "[!] git이 필요합니다 (git not found)." >&2; return 1; }
    if [ -d "$DL_DIR/.git" ]; then
        echo "[*] 기존 소스 갱신 중: $DL_DIR" >&2
        ( cd "$DL_DIR" && git pull --ff-only ) >&2 2>&1 || true
    else
        echo "[*] QuickBMS 내려받는 중: $DL_URL" >&2
        rm -rf "$DL_DIR"
        git clone --depth 1 "$DL_URL" "$DL_DIR" >&2 || return 1
    fi
    normalize_src "$DL_DIR"
}

# --- resolve the source directory ------------------------------------------
ARG="${1:-}"
SRC=""
if [ "$ARG" = "--download" ]; then
    SRC="$(clone_quickbms)" || { echo "[!] 다운로드/소스 확보 실패."; exit 1; }
else
    CAND="${ARG:-${QUICKBMS_SRC:-}}"
    if [ -n "$CAND" ]; then
        # An explicit path was given: use it, or fail clearly. Do NOT silently
        # fall back to auto-detecting some other checkout.
        if ! SRC="$(normalize_src "$CAND")"; then
            echo "[!] 지정한 경로에서 src/quickbms.c 를 찾지 못했습니다:"
            echo "      $CAND"
            echo "    QuickBMS 저장소 루트, 또는 그 안의 src 폴더를 지정하세요."
            exit 1
        fi
    else
        # No path given: auto-detect a QuickBMS checkout next to noesis-llsifac,
        # in your home folder, or a previous --download cache.
        for guess in \
            "$TOOLS_DIR/../../QuickBMS" "$TOOLS_DIR/../../quickbms" \
            "$TOOLS_DIR/../QuickBMS"    "$TOOLS_DIR/../quickbms" \
            "$HOME/QuickBMS"            "$HOME/quickbms" \
            "$DL_DIR"; do
            if S="$(normalize_src "$guess")"; then SRC="$S"; break; fi
        done
    fi
fi

if [ -z "$SRC" ]; then
    cat <<EOF
[!] QuickBMS 소스를 찾지 못했습니다. (QuickBMS source not found.)

  'QuickBMS 소스 경로'란 = QuickBMS 저장소를 받아둔 폴더입니다.
  (그 안에 src/quickbms.c 와 Makefile 이 들어 있습니다.)

  방법 1) 이미 받아두셨다면 그 폴더를 알려주세요:
     ./build_quickbms_macos.sh /Users/<당신>/quickbms
  방법 2) 자동으로 내려받아 빌드:
     ./build_quickbms_macos.sh --download

  팁: QuickBMS 폴더를 noesis-llsifac 와 같은 위치(형제 폴더)에 두면
      경로 없이도 자동으로 찾습니다.
EOF
    exit 1
fi

SRC="$(cd "$SRC" && pwd)"
echo "[*] QuickBMS source: $SRC"

# --- need a compiler --------------------------------------------------------
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
    echo "[!] Xcode Command Line Tools가 필요합니다. 설치 후 다시 실행:"
    echo "      xcode-select --install"
    exit 1
fi

mkdir -p "$BIN_DIR"

build_succeeded() {
    [ -x "$SRC/quickbms" ] || [ -x "$SRC/quickbms_4gb_files" ]
}

copy_binary() {
    for b in quickbms_4gb_files quickbms; do
        if [ -x "$SRC/$b" ]; then
            cp -f "$SRC/$b" "$BIN_DIR/$b"
            chmod +x "$BIN_DIR/$b"
            echo "[ok] 설치됨 (installed): $BIN_DIR/$b"
        fi
    done
}

echo "[*] 1/2: 기본 'make' 시도…"
( cd "$SRC" && make clean >/dev/null 2>&1; make ) 2>&1 | tail -n 20

if build_succeeded; then
    copy_binary
    echo "[ok] 빌드 완료 (build complete)."
    exit 0
fi

echo
echo "[*] 기본 빌드 실패(보통 32비트 -m32 때문). 64비트로 재시도합니다…"
echo

# 64-bit, OpenSSL disabled (SSL is only used by the optional self-update
# feature, not by extraction). Everything is forced on the command line so the
# Makefile's 32-bit / brew-openssl appends are bypassed.
#   * SRC=quickbms.c  : the Makefile derives SRC from EXE ($(EXE).c), so when we
#     rename EXE to quickbms_4gb_files we must point SRC back at the real file.
#   * -Wno-implicit-* / -Wno-int-conversion / -Wno-incompatible-pointer-types :
#     modern clang (Xcode 15+) promotes these old-C patterns to hard errors;
#     downgrade them so QuickBMS's bundled libraries compile.
PERMISSIVE="-Wno-implicit-function-declaration -Wno-int-conversion -Wno-incompatible-pointer-types -Wno-implicit-int"
EXTRA_CFLAGS=""
if [ "$(uname -s)" = "Darwin" ]; then
    EXTRA_CFLAGS="-Dunix -DFORCE_SATUR_SUB_128"
fi

( cd "$SRC" && make clean >/dev/null 2>&1
  make EXE=quickbms_4gb_files SRC=quickbms.c USE_OPENSSL= \
       CFLAGS="-O2 -w -fno-pie -fPIC -DQUICKBMS64 $PERMISSIVE $EXTRA_CFLAGS" \
       CDEFS="-DDISABLE_MCRYPT -DDISABLE_TOMCRYPT -DDISABLE_SSL -DZSTD_DISABLE_ASM -DQUICKBMS64 -ldl" \
       CLIBS="-lstdc++ -lm -lpthread" ) 2>&1 | tail -n 40

if build_succeeded; then
    copy_binary
    echo "[ok] 64비트 빌드 완료 (build complete)."
    exit 0
fi

cat <<EOF

[!] 자동 빌드에 실패했습니다. (Automatic build failed.)

    수동 빌드 (build manually):
      cd "$SRC"
      make EXE=quickbms_4gb_files SRC=quickbms.c USE_OPENSSL= \\
        CFLAGS="-O2 -w -fno-pie -fPIC -DQUICKBMS64 -Dunix -DFORCE_SATUR_SUB_128 $PERMISSIVE" \\
        CDEFS="-DDISABLE_MCRYPT -DDISABLE_TOMCRYPT -DDISABLE_SSL -DZSTD_DISABLE_ASM -DQUICKBMS64 -ldl" \\
        CLIBS="-lstdc++ -lm -lpthread"

    빌드된 실행 파일을 여기로 복사하면 GUI가 자동으로 인식합니다:
      cp quickbms_4gb_files "$BIN_DIR/"

    빌드가 계속 실패하면, QuickBMS 컴파일 없이 .arc 를 푸는
    'sifac_native.py' (순수 파이썬) 를 사용하세요. GUI의 '네이티브(파이썬)
    추출' 옵션을 켜면 됩니다.
EOF
exit 1
