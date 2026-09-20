#!/usr/bin/env bash
# Install into a new empty prefix, or open that prefix. Does NOT isolate networking.
set -euo pipefail
die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID != 0 ]] || die 'Run as your ordinary desktop user, not root.'
[[ $# -ge 2 ]] || die 'Usage: bash agent-debian12.sh install|open /absolute/build-area [installer.msi]'
mode=$1
work=$2
[[ $work == /* && -d $work ]] || die 'The build area must be an existing absolute directory.'
work=$(cd -- "$work" && pwd -P)
[[ -x $work/wine-install/bin/wine && -x $work/wine-install/bin/wineserver ]] ||
    die 'The private Wine installation is missing.'
[[ -n ${DISPLAY:-} ]] || die 'Use a terminal in your X11 desktop session.'
export PATH="$work/wine-install/bin:$PATH"
export WINEPREFIX="$work/agent-prefix"
export WINEARCH=wow64
export WINEDLLOVERRIDES='mscoree=d;winemenubuilder.exe=d'
case $mode in
    install)
        [[ $# == 3 ]] || die 'Install requires the path to your own Agent MSI.'
        [[ ! -e $WINEPREFIX && ! -L $WINEPREFIX ]] || die 'Prefix already exists; refusing to replace or reinstall it.'
        installer=$(realpath -e -- "$3")
        [[ -f $installer && $installer == *.msi ]] || die 'Provide a regular .msi installer file.'
        [[ ! -e $work/agent-install.log && ! -L $work/agent-install.log ]] || die 'Install log already exists; stop and investigate.'
        installer_windows=$(winepath -w "$installer")
        log_windows=$(winepath -w "$work/agent-install.log")
        wine msiexec /i "$installer_windows" /qn /norestart /L*v "$log_windows"
        wineserver -w
        test -f "$WINEPREFIX/drive_c/Program Files (x86)/Agent/agent.exe"
        printf 'Agent installed; no account or mail archive imported.\n'
        ;;
    open)
        [[ $# == 2 ]] || die 'Open takes only the build-area argument.'
        [[ -d $WINEPREFIX && ! -L $WINEPREFIX ]] || die 'Expected an independent prefix directory, not a symlink.'
        test -f "$WINEPREFIX/drive_c/Program Files (x86)/Agent/agent.exe"
        printf 'This recipe does not block networking. Do not import accounts or poll during the first test.\n'
        wine 'C:\Program Files (x86)\Agent\agent.exe'
        wineserver -w
        printf 'Agent and its Wine processes have exited normally.\n'
        ;;
    *) die 'Choose install or open.' ;;
esac
