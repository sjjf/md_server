#
# Copyright 2020 Australian National University
#
# Please see the LICENSE.txt file for details.

import logging
import os
import shutil
import signal
from pathlib import Path

config_template = """
## WARNING:  THIS IS AN AUTO-GENERATED FILE. MANUAL CHANGES WILL BE
## OVERWRITTEN.
#
# This file is managed by mdserver - changes should be made through the
# mdserver config most likely in /etc/mdserver.
user={user}
leasefile-ro
strict-order
expand-hosts
pid-file={run_dir}/{net_name}.pid
{except_interface}
{listen_address}
interface={interface}
dhcp-range={mds_gateway},static
dhcp-no-override
dhcp-lease-max={lease_len}
dhcp-hostsfile={dhcp_hostsfile}
dhcp-optsfile={dhcp_optsfile}
hostsdir={dns_hostsdir}
"""

opts_template = """
option:classless-static-route,{mds_addr}/32,{mds_gateway},0.0.0.0/0,{mds_gateway}
249,{mds_addr}/32,{mds_gateway},0.0.0.0/0,{mds_gateway}
option:router,{mds_gateway}
"""

logger = logging.getLogger("mdserver.dnsmasq")


class Dnsmasq(object):
    """Manage dnsmasq configuration.

    This code is primarily concerned with managing host data for dnsmasq's
    DHCP and DNS configuration, as well as creating a configuration file that
    makes use of that host data.
    """

    def __init__(self, config):
        self.config = config
        for option in config:
            if option.startswith("dnsmasq."):
                name = option.split(".")[1]
                setattr(self, name, config[option])
        self.pidfile = os.path.join(self.run_dir, self.net_name + ".pid")
        self.base_dir = Path(self.base_dir).resolve().as_posix()

    def hup(self):
        """Send a SIGHUP to the dnsmasq process, triggering a reload of the
        updated dhcp/dns files."""

        # Note that this may not have any effect on anything, but it should not
        # fail - it may not do anything if dnsmasq isn't running, or if the
        # pidfile is out of date, but it should never cause the server to fall
        # over unless an unexpected error occurs.
        try:
            logger.debug("HUPing dnsmasq")
            with open(self.pidfile, "r") as pf:
                line = pf.read()
                pid = int(line)
                os.kill(pid, signal.SIGHUP)
                logger.info("HUPed dnsmasq[%d]", pid)
        except OSError as e:
            logger.info("Failed to HUP dnsmasq: %s", e)
        except ValueError as e:
            logger.info("Failed to parse dnsmasq pid: %s", e)
            pass

    def gen_dhcp_hosts(self, db):
        """Create a dnsmasq DHCP hosts file.

        All host data is pulled from the database, and is written to a single
        file, overwriting any previous data.
        """
        lease = self.lease_len
        dirname = os.path.join(self.base_dir, "dhcp")
        Path(dirname).mkdir(mode=0o777, parents=True, exist_ok=True)
        name = self.net_name + ".dhcp-hosts"
        hostsfile = os.path.join(dirname, name)
        # note that this truncates the file before writing
        with open(hostsfile, "w") as hf:
            lcount = 0
            for entry in db:
                mac = entry["mds_mac"]
                ipv4 = entry["mds_ipv4"]
                ipv6 = entry["mds_ipv6"]
                hname = entry["domain_name"]
                if ipv4 is not None:
                    line = "%s,id:*,%s,%s,%d\n" % (mac, ipv4, hname, lease)
                    hf.write(line)
                    lcount += 1
                if ipv6 is not None:
                    line = "%s,id:*,[%s],%s,%d\n" % (mac, ipv6, hname, lease)
                    hf.write(line)
                    lcount += 1
            logger.debug("Wrote %d lines to %s", lcount, hostsfile)

    def gen_dns_hosts(self, db):
        """Create a dnsmasq DNS hosts file.

        All host data is pulled from the database, and is written to a single
        file, overwriting any previous data.
        """
        order = self.entry_order
        order = [o.strip().lower() for o in order.split(",")]
        prefix = self.prefix
        domain = self.domain
        dirname = os.path.join(self.base_dir, "dns")
        Path(dirname).mkdir(mode=0o777, parents=True, exist_ok=True)
        name = self.net_name + ".dns-hosts"
        hostsfile = os.path.join(dirname, name)
        # note that this truncates the file befor writing
        with open(hostsfile, "w") as hf:
            lcount = 0
            for entry in db:
                ipv4 = entry["mds_ipv4"]
                ipv6 = entry["mds_ipv6"]
                hname = entry["domain_name"]
                prefixed = hname
                if prefix:
                    prefixed = prefix + hname
                fqdn = False
                if domain:
                    fqdn = prefixed + "." + domain
                names = []
                for o in order:
                    if o.startswith("base"):
                        names.append(hname)
                    elif o.startswith("prefix"):
                        if prefix:
                            names.append(prefixed)
                    elif o == "domain" or o == "fqdn":
                        if domain:
                            names.append(fqdn)
                if len(names) > 0:
                    if ipv4 is not None:
                        line = "%s %s\n" % (ipv4, " ".join(names))
                        hf.write(line)
                        lcount += 1
                    if ipv6 is not None:
                        line = "%s %s\n" % (ipv6, " ".join(names))
                        hf.write(line)
                        lcount += 1
            logger.debug("Wrote %d lines to %s", lcount, hostsfile)

    def gen_dnsmasq_config(self):
        """Create a dnsmasq config file, set up to make use of generated host
        data, along with other relevant configuration options.
        """
        logger.info("Creating dnsmasq config in %s", self.base_dir)
        # make basedir
        Path(self.base_dir).mkdir(mode=0o775, parents=False, exist_ok=True)
        try:
            shutil.chown(self.base_dir, user=None, group=self.user)
        except PermissionError:
            pass
        # make dhcp and dns dirs
        confname = self.net_name + ".conf"
        conffile = os.path.join(self.base_dir, confname)
        optsname = self.net_name + ".opts"
        optsfile = os.path.join(self.base_dir, optsname)
        dhcp_dir = os.path.join(self.base_dir, "dhcp")
        Path(dhcp_dir).mkdir(mode=0o775, parents=True, exist_ok=True)
        try:
            shutil.chown(dhcp_dir, user=self.user, group=self.user)
        except PermissionError:
            pass
        dns_dir = os.path.join(self.base_dir, "dns")
        Path(dns_dir).mkdir(mode=0o775, parents=True, exist_ok=True)
        try:
            shutil.chown(dns_dir, user=self.user, group=self.user)
        except PermissionError:
            pass
        # make run dir
        Path(self.run_dir).mkdir(mode=0o775, parents=False, exist_ok=True)
        try:
            shutil.chown(self.base_dir, user=self.user, group=self.user)
        except PermissionError:
            pass

        # special-case this, since if we try to listen on lo (for testing) we
        # won't be able to without some tweaking
        except_interface = "except-interface=lo"
        listen_address = "# no listen address defined"
        if self.listen_address is not None:
            listen_address = "listen-address={}".format(self.listen_address)
            if self.listen_address.startswith("127.") and self.interface == "lo":
                except_interface = "# don't ignore lo"

        config_strings = {
            "user": self.user,
            "net_name": self.net_name,
            "interface": self.interface,
            "except_interface": except_interface,
            "listen_address": listen_address,
            "lease_len": self.lease_len,
            "run_dir": self.run_dir,
            "dhcp_hostsfile": dhcp_dir,
            "dns_hostsdir": dns_dir,
            "dhcp_optsfile": optsfile,
            "mds_gateway": self.gateway,
        }
        opts_strings = {
            "mds_gateway": self.gateway,
            "mds_addr": self.config["mdserver.listen_address"],
        }

        config_formatted = config_template.format(**config_strings)
        opts_formatted = opts_template.format(**opts_strings)
        if self.config["dnsmasq.domain"] is not None:
            config_formatted += "domain={}\n".format(self.domain)
        if self.config["dnsmasq.use_dns"]:
            opts_formatted += "option:dns-server,{}\n".format(self.gateway)
        with open(conffile, "w") as cf:
            cf.write(config_template.format(**config_strings))
            logger.info("Wrote dnsmasq config to %s", conffile)
        with open(optsfile, "w") as of:
            of.write(opts_template.format(**opts_strings))
            logger.info("Wrote dnsmasq options to %s", optsfile)
