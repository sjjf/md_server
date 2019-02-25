#!/usr/bin/env python
# query and manipulate a dnsmasq server
#
# Copyright 2016-2019 Australian National University
#
# Please see the LICENSE.txt file for details.

import os
import signal
import sys


# I think we can safely assume that the config will be fairly fixed
class Dnsmasq(object):
    def __init__(self, conffile):
        self.conffile = conffile
        self._load_config()
        self._read_addn_hosts()

    def _load_config(self):
        self.config = {}
        with open(self.conffile) as config:
            lines = config.readlines()
            for line in lines:
                try:
                    key, value = line.strip().split('=')
                    self.config[key] = value
                except ValueError:
                    self.config[line] = "yes"

        return self.config

    def _read_addn_hosts(self):
        self.addn_hosts = {}
        with open(self.config['addn-hosts']) as ahosts:
            lines = ahosts.readlines()
            for line in lines:
                ip, names = line.strip().split(None, 1)
                self.addn_hosts[ip] = []
                for name in names.split():
                    self.addn_hosts[ip].append(name)
        return self.addn_hosts

    def _write_addn_hosts(self):
        with open(self.config['addn-hosts'], 'w+') as ahosts:
            ips = self.addn_hosts.keys()
            ips.sort()
            for ip in ips:
                names = self.addn_hosts[ip]
                names.insert(0, ip)
                ahosts.write("%s\n" % (" ".join(names)))
            ahosts.close()
            # make dnsmasq re-read the file
            with open(self.config['pid-file']) as pidfile:
                pid = int(pidfile.readline())
                os.kill(pid, signal.SIGHUP)

    def get_addn_host_by_ip(self, ip):
        """Return the name currently assigned to this IP"""
        try:
            return self.addn_hosts[ip]
        except KeyError:
            return []

    def get_addn_host_by_name(self, name):
        """Return the IPs currently mapped to this host"""
        rev = {}
        for ip in self.addn_hosts.keys():
            names = self.addn_hosts[ip]
            for name in names:
                if name not in rev:
                    rev[name] = [ip]
                else:
                    rev[name] = rev[name].append(ip)
        try:
            return rev[name]
        except KeyError:
            return []

    def del_addn_host(self, ip):
        """Remove the entry for this IP"""
        try:
            del(self.addn_hosts[ip])
        except KeyError:
            pass
        self._write_addn_hosts()
        self._read_addn_hosts()

    def update_addn_host(self, ip, name):
        """Add the given name to DNS for this IP"""
        try:
            if name not in self.addn_hosts[ip]:
                self.addn_hosts[ip].append(name)
        except KeyError:
            self.addn_hosts[ip] = [name]
        self._write_addn_hosts()
        self._read_addn_hosts()

    def set_addn_host(self, ip, names):
        """Replace the current entry in DNS for this IP with a list of
        hostnames."""
        if not isinstance(names, list):
            raise ValueError("List of hostnames required")
        self.addn_hosts[ip] = names
        self._write_addn_hosts()
        self._read_addn_hosts()


if __name__ == "__main__":
    d = Dnsmasq('/tmp/mds.conf')
    hosts = d.get_addn_host_by_ip(sys.argv[1])
    print(hosts)
    d.set_addn_host(sys.argv[1], "new")
    hosts = d.get_addn_host_by_ip(sys.argv[1])
    print(hosts)
    d.update_addn_host(sys.argv[1], "added")
    hosts = d.get_addn_host_by_ip(sys.argv[1])
    print(hosts)
    d.del_addn_host(sys.argv[1])
    hosts = d.get_addn_host_by_ip(sys.argv[1])
    print(hosts)
    print(d.addn_hosts)
