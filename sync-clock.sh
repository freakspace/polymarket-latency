#!/bin/bash
#
# Clock Synchronization Script for Ubuntu/Debian VPS
# Syncs system clock with NTP servers for accurate latency measurements
#

set -e

echo "=========================================="
echo "Clock Synchronization Script"
echo "=========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ This script must be run as root (use sudo)"
    exit 1
fi

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Display current time status
echo "Current time status:"
date
echo ""

# Check if systemd-timesyncd is available and active
if command_exists timedatectl; then
    echo "Checking systemd time sync status..."
    timedatectl status
    echo ""
fi

# Install ntpdate if not present
if ! command_exists ntpdate; then
    echo "📦 Installing ntpdate..."
    apt-get update -qq
    apt-get install -y ntpdate
    echo "✓ ntpdate installed"
    echo ""
fi

# Temporarily stop systemd-timesyncd if running (it conflicts with ntpdate)
TIMESYNCD_WAS_ACTIVE=false
if systemctl is-active --quiet systemd-timesyncd 2>/dev/null; then
    echo "⏸  Temporarily stopping systemd-timesyncd..."
    systemctl stop systemd-timesyncd
    TIMESYNCD_WAS_ACTIVE=true
fi

# Sync the clock
echo "🔄 Syncing clock with NTP servers..."
echo ""

# Try multiple NTP servers for reliability
NTP_SERVERS=(
    "pool.ntp.org"
    "time.nist.gov"
    "time.google.com"
)

SYNC_SUCCESS=false
for server in "${NTP_SERVERS[@]}"; do
    echo "Trying $server..."
    if ntpdate -u "$server" 2>/dev/null; then
        SYNC_SUCCESS=true
        echo "✓ Successfully synced with $server"
        break
    else
        echo "⚠  Failed to sync with $server"
    fi
done

echo ""

if [ "$SYNC_SUCCESS" = false ]; then
    echo "❌ Failed to sync with any NTP server"
    echo "   Check your internet connection and firewall settings"
    exit 1
fi

# Restart systemd-timesyncd if it was running
if [ "$TIMESYNCD_WAS_ACTIVE" = true ]; then
    echo "▶️  Restarting systemd-timesyncd..."
    systemctl start systemd-timesyncd
fi

# Display new time
echo ""
echo "=========================================="
echo "✓ Clock synchronization complete!"
echo "=========================================="
echo ""
echo "New system time: $(date)"
echo "UTC time: $(date -u)"
echo ""

# Optional: Enable continuous time sync
echo "Do you want to enable continuous time synchronization? (y/n)"
echo "(This will keep your clock synced automatically)"
read -r -p "> " response

if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    if command_exists timedatectl; then
        echo ""
        echo "Enabling systemd-timesyncd for continuous sync..."
        timedatectl set-ntp true
        systemctl enable systemd-timesyncd
        systemctl start systemd-timesyncd
        echo "✓ Continuous time sync enabled"
        echo ""
        timedatectl status
    else
        echo ""
        echo "Installing and configuring ntp daemon..."
        apt-get install -y ntp
        systemctl enable ntp
        systemctl start ntp
        echo "✓ NTP daemon installed and started"
    fi
else
    echo ""
    echo "⚠  Continuous sync not enabled."
    echo "   Run this script again before each measurement session,"
    echo "   or enable it manually with: sudo timedatectl set-ntp true"
fi

echo ""
echo "You can now run latency measurements with accurate timestamps."
