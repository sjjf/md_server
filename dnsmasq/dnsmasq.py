#!/usr/bin/env python
# query and manipulate a dnsmasq server

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

    def get_addn_host(self, ip):
        """Return the name currently assigned to this IP"""
        try:
            return self.addn_hosts[ip]
        except KeyError:
            return []

    def update_addn_host(self, ip, name):
        """Add the given name to DNS for this IP"""
        try:
            if name not in self.addn_hosts[ip]:
                self.addn_hosts[ip].append(name)
            print self.addn_hosts[ip]
        except KeyError:
            self.addn_hosts[ip] = [name]
        self._write_addn_hosts()
        self._read_addn_hosts()

    def set_addn_host(self, ip, name):
        """Replace the current name in DNS for this IP"""
        self.addn_hosts[ip] = [name]
        self._write_addn_hosts()
        self._read_addn_hosts()

if __name__ == "__main__":
    d = Dnsmasq('/tmp/mds.addnhosts')
    hosts = d.get_addn_host(sys.argv[1])
    print hosts
    d.set_addn_host(sys.argv[1], "new")
    hosts = d.get_addn_host(sys.argv[1])
    print hosts
    d.update_addn_host(sys.argv[1], "added")
    hosts = d.get_addn_host(sys.argv[1])
    print hosts
