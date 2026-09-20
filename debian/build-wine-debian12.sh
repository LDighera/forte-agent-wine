#!/usr/bin/env bash
# Manual build recipe accompanying DEBIAN12.md; no sudo or host package changes.
set -euo pipefail
die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# == 1 ]] || die 'Usage: bash build-wine-debian12.sh /absolute/new/build-area'
[[ $EUID != 0 ]] || die 'Run as your ordinary desktop user, not root.'
work=$1
[[ $work == /* && $work != / && ! -e $work && ! -L $work ]] ||
    die 'Choose an absolute, nonexistent destination; existing work is never reused.'
[[ $(uname -m) == x86_64 ]] || die 'This recipe targets Debian 12 amd64.'
# shellcheck disable=SC1091
. /etc/os-release
[[ $ID == debian && $VERSION_ID == 12 ]] || die 'This recipe targets Debian 12 only.'
export PATH=/usr/lib/llvm-14/bin:$PATH
for tool in git curl gcc g++ make flex bison pkg-config msgfmt clang lld-link llvm-dlltool; do
    command -v "$tool" >/dev/null || die "Missing prerequisite: $tool"
done
pkg-config --exists x11 xext xrender xrandr xi xcursor xfixes freetype2 fontconfig gnutls gl ||
    die 'Required development libraries are missing; review debian12-packages.txt.'
jobs=${WINE_BUILD_JOBS:-4}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || die 'WINE_BUILD_JOBS must be a positive integer.'
mkdir -- "$work"
work=$(cd -- "$work" && pwd -P)
git clone --depth 1 --branch wine-11.14 https://github.com/wine-mirror/wine.git "$work/source"
[[ $(git -C "$work/source" rev-parse HEAD) == 1012f3d99507b80d4869eabf0853567660a7ecbb ]] ||
    die 'The release checkout differs from the recorded Wine 11.14 commit.'
curl --fail --location --proto '=https' --proto-redir '=https' \
    https://github.com/wine-mirror/wine/commit/a1bae27f21b53fa3ac6be6b08f4cbfb441360ade.patch \
    --output "$work/input-fix.patch"
git -C "$work/source" apply --check "$work/input-fix.patch"
git -C "$work/source" apply "$work/input-fix.patch"
[[ $(git -C "$work/source" status --porcelain --untracked-files=all) == ' M dlls/win32u/input.c' ]] ||
    die 'Unexpected patched source paths.'
[[ $(git -C "$work/source" hash-object --no-filters dlls/win32u/input.c) == 71219aed5fec18e9c30d5f0d098742814b7248b6 ]] ||
    die 'The patched input file does not match the recorded upstream fix.'
mkdir -- "$work/build"
cd -- "$work/build"
../source/configure --prefix="$work/wine-install" --enable-archs=i386,x86_64 --with-mingw=clang \
    2>&1 | tee configure-output.txt
make -j"$jobs" 2>&1 | tee build-output.txt
make install 2>&1 | tee install-output.txt
test -x "$work/wine-install/bin/wineserver"
test -f "$work/wine-install/lib/wine/i386-windows/win32u.dll"
test -f "$work/wine-install/lib/wine/x86_64-windows/win32u.dll"
[[ $("$work/wine-install/bin/wine" --version) == wine-11.14 ]] || die 'Unexpected Wine version.'
printf 'Wine build complete: %s\nNo Agent prefix was created.\n' "$work/wine-install"
