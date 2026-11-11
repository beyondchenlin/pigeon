#!/data/data/com.termux/files/usr/bin/bash
# Existing single-phone maintenance tunnel. Never pairs or toggles debugging.
set -u

discovered_port() {
  local file="$1" now="$2" stamp value
  [[ -f "$file" ]] || return 1
  read -r stamp value < "$file" || return 1
  [[ "$stamp" =~ ^[0-9]{10}$ && "$value" =~ ^[0-9]{4,5}$ ]] || return 1
  (( now >= 10#$stamp && now - 10#$stamp <= 45 && 10#$value >= 1024 && 10#$value <= 65535 )) || return 1
  printf '%s\n' "$value"
}

main() {
  export HOME=/data/data/com.termux/files/home PREFIX=/data/data/com.termux/files/usr
  export PATH="$PREFIX/bin:/system/bin"
  local pilot_dir="$HOME/phone-pilot" ssh_pid='' port fresh last_state=''
  # An explicit local opt-in is required even when Termux's shell starts us.
  [[ -f "$pilot_dir/maintenance.enabled" ]] || return 0
  umask 077
  mkdir -p "$pilot_dir"
  chmod 700 "$pilot_dir"
  exec 9>"$pilot_dir/tunnel.lock"
  flock -n 9 || { printf '维护连接已在运行。\n'; return 0; }
  printf '%s\n' "$$" > "$pilot_dir/tunnel.pid"
  cleanup() {
    [[ -z "$ssh_pid" ]] || kill "$ssh_pid" 2>/dev/null || true
    rm -f "$pilot_dir/tunnel.pid"
    termux-wake-unlock >/dev/null 2>&1 || true
  }
  trap cleanup EXIT
  trap 'exit 0' INT TERM HUP
  termux-wake-lock >/dev/null 2>&1 || true
  probe_port() {
    [[ "$1" =~ ^[0-9]{4,5}$ ]] && ((10#$1 >= 1024 && 10#$1 <= 65535)) || return 1
    timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/"$1"' _ "$1" 2>/dev/null
  }
  rotate_log() {
    local file="$pilot_dir/tunnel.log" index
    if [[ -f "$file" ]] && (( $(wc -c < "$file") >= 20971520 )); then
      rm -f "$file.4"
      for index in 3 2 1; do [[ ! -f "$file.$index" ]] || mv "$file.$index" "$file.$((index+1))"; done
      mv "$file" "$file.1"
    fi
  }
  port=$(cat "$pilot_dir/adb-port" 2>/dev/null || true)
  [[ "$port" =~ ^[0-9]{4,5}$ ]] || port=1024
  printf '维护助手已启动；无线调试端口由手机自动发现。正常 5G 远控无需重复配对。\n'
  while [[ -f "$pilot_dir/maintenance.enabled" ]]; do
    fresh=$(discovered_port "$pilot_dir/adb-discovered-port" "$(date +%s)" || true)
    if [[ -n "$fresh" ]]; then port="$fresh"; fi
    printf '%s\n' "$port" > "$pilot_dir/adb-port"
    chmod 600 "$pilot_dir/adb-port"
    rotate_log
    ssh -N -T -i "$HOME/.ssh/phone-pilot" \
      -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
      -o UserKnownHostsFile="$HOME/.ssh/phone-pilot-known-hosts" \
      -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes -E "$pilot_dir/tunnel.log" \
      -R "127.0.0.1:15555:127.0.0.1:$port" \
      -R 127.0.0.1:16502:127.0.0.1:65000 phone-pilot@47.242.93.105 &
    ssh_pid=$!
    while kill -0 "$ssh_pid" 2>/dev/null; do
      sleep 5
      [[ -f "$pilot_dir/maintenance.enabled" ]] || return 0
      # ssh keeps its log descriptor open. Stop this owned child before the
      # outer loop rotates, otherwise it can keep growing the renamed file.
      if [[ -f "$pilot_dir/tunnel.log" ]] && (( $(wc -c < "$pilot_dir/tunnel.log") >= 20971520 )); then
        kill "$ssh_pid" 2>/dev/null || true; break
      fi
      fresh=$(discovered_port "$pilot_dir/adb-discovered-port" "$(date +%s)" || true)
      if [[ -n "$fresh" && "$fresh" != "$port" ]]; then
        port="$fresh"; kill "$ssh_pid" 2>/dev/null || true; break
      fi
      if probe_port "$port"; then
        [[ "$last_state" == ready ]] || printf '维护通道已就绪。\n'
        last_state=ready
      else
        [[ "$last_state" == waiting ]] || printf '等待系统无线调试端口恢复；无需输入端口，远控服务由独立通道连接。\n'
        last_state=waiting
      fi
    done
    wait "$ssh_pid" 2>/dev/null || true
    ssh_pid=''
    sleep 5
  done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
