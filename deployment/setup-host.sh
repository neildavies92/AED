#!/bin/sh
# Run only on the owner-approved new Ubuntu VPS, after a second SSH key login works.
set -eu
umask 077
[ "$(id -u)" = 0 ] || { echo 'Run as root on the approved host.' >&2; exit 1; }
[ "$#" = 1 ] || { echo 'Usage: setup-host.sh ADMIN_SSH_CIDR' >&2; exit 1; }
python3 -c 'import ipaddress,sys; ipaddress.ip_network(sys.argv[1], strict=False)' "$1"
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || { echo 'Ubuntu 24.04 required.' >&2; exit 1; }
# The known deployment host uses UTC cron; application time remains Europe/London.
apt-get update
apt-get upgrade -y
apt-get install -y ca-certificates curl cron ufw python3 logrotate unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/aed-docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if ! getent group aed >/dev/null; then groupadd --gid 10001 aed; fi
if ! id aed >/dev/null 2>&1; then useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin aed; fi
[ "$(id -u aed)" = 10001 ] && [ "$(id -g aed)" = 10001 ]
# Never add an interactive user to the root-equivalent docker group.
install -d -m 0700 /srv/aed /srv/aed/backups /srv/aed/logs
install -d -m 0750 -o root -g 10001 /srv/aed/config
install -d -m 0700 -o 10001 -g 10001 /srv/aed/state /srv/aed/credentials
install -d -m 0755 /usr/local/lib/aed
cat > /etc/ssh/sshd_config.d/00-aed.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
SSH
chmod 0600 /etc/ssh/sshd_config.d/00-aed.conf
/usr/sbin/sshd -t
systemctl reload ssh
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$1" to any port 22 proto tcp
ufw --force enable
# No Docker-published ports. Mirror SSH-only restrictions in the provider firewall.
timedatectl set-timezone UTC
timedatectl set-ntp true
systemctl enable --now docker cron
systemctl restart cron
docker version --format '{{.Server.Version}}'
docker compose version
timedatectl show -p Timezone -p NTPSynchronized
printf '%s\n' 'Host prepared. No AED cron or authorization installed. Reboot if required; verify second SSH login and cloud firewall.'
