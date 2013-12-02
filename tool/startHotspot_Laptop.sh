#!/bin/bash
# Start
# Configure IP address for WLAN
sudo ifconfig wlan1 192.168.150.1

# Start DHCP/DNS server
sudo service dnsmasq stop
sudo dnsmasq -I lo

# Run access point daemon
sudo hostapd /etc/hostapd.conf

