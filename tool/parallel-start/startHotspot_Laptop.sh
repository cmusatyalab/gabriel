#!/bin/bash

sudo service dnsmasq stop
sudo service hostapd stop

# Start
# Configure IP address for WLAN
sudo ifconfig wlan1 192.168.150.1

# Start DHCP/DNS server
sudo dnsmasq -I lo
# !!! If this reports "dnsmasq: failed to create listening socket for 192.168.150.1: Address already in use"
# !!! kill the other processes running dnsmasq and try again.

# Run access point daemon
sudo hostapd /etc/hostapd.conf

