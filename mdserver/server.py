import bottle
import json
import libvirt
import logging
import os
import sys

from bottle import route, run, template, request, response, install
from datetime import datetime
from distutils.util import strtobool
from dnsmasq.dnsmasq import Dnsmasq
from functools import wraps
from xml.dom import minidom


USERDATA_TEMPLATE = """\
#cloud-config
hostname: {{hostname}}
local-hostname: {{hostname}}
fqdn: {{hostname}}.localdomain
manage_etc_hosts: true
ssh_authorized_keys:
    - {{public_key_default}}
"""


logger = logging.getLogger(__name__ + "_file")


def log_to_logger(fn):
    '''
    Wrap a Bottle request so that a log line is emitted after it's handled.
    (This decorator can be extended to take the desired logger as a param.)
    '''
    @wraps(fn)
    def _log_to_logger(*args, **kwargs):
        request_time = datetime.now()
        actual_response = fn(*args, **kwargs)
        # modify this to log exactly what you need:
        logger.info('%s %s %s %s %s' % (request.remote_addr,
                                        request_time,
                                        request.method,
                                        request.url,
                                        response.status))
        return actual_response
    return _log_to_logger


class MetadataHandler(object):

    def __init__(self):
        self.dnsmasq = None

    def _set_dnsmasq_handler(self, dnsmasq):
        self.dnsmasq = dnsmasq

    def _update_dnsmasq(self, ip, name):
        """Update the dnsmasq additional hosts file."""
        if not self.dnsmasq:
            return
        config = bottle.request.app.config
        prefixed = config['dnsmasq.prefix'] + name
        fqdn = prefixed + '.' + config['dnsmasq.domain']
        self.dnsmasq.set_addn_host(ip, fqdn)
        self.dnsmasq.update_addn_host(ip, prefixed)
        self.dnsmasq.update_addn_host(ip, name)

    def _get_all_domains(self):
        conn = libvirt.open()
        return conn.listAllDomains(0)

    # filters work by specifying a tag, and a set of attributes on that tag
    # which need to be matched: {'tag': 'source', 'attrs': {'network': 'mds'}}
    # matches source tags that have the network attribute set to 'mds'. Only a
    # single tag is supported, but potentially more than one attribute. An
    # empty filter means return all interfaces
    def _get_domain_interfaces(self, domain, filter={}):
        raw_xml = domain.XMLDesc(0)
        xml = minidom.parseString(raw_xml)
        interfaces = xml.getElementsByTagName('interface')
        try:
            tag = filter['tag']
            attrs = filter['attrs']
        except KeyError:
            return interfaces

        # this is a bit klunky, but the data structure we're testing is fiddly
        # to work with.
        #
        # We start by finding a node that matches the tag, and then we check
        # that the node has all the attributes that we're matching against,
        # then we check that all those attributes match the filter.
        accum = []
        for interface in interfaces:
            nodes = interface.childNodes
            for node in nodes:
                if node.nodeName == tag:
                    required = len(attrs.keys())
                    for attr in node.attributes.keys():
                        if attr in attrs:
                            if node.attributes[attr].value == attrs[attr]:
                                required -= 1
                    if required == 0:
                        accum.append(interface)
        return accum

    def _get_mac_from_interface(self, interface):
        nodes = interface.childNodes
        for node in nodes:
            if node.nodeName == 'mac':
                return node.attributes['address'].value

    def _get_domain_macs(self, network):
        macs = {}
        domains = self._get_all_domains()
        net_filter = {
            'tag': 'source',
            'attrs': {
                'network': network,
            }
        }
        for domain in domains:
            interfaces = self._get_domain_interfaces(domain, filter=net_filter)
            for interface in interfaces:
                mac = self._get_mac_from_interface(interface)
                macs[mac] = domain
        return macs

    def _get_mgmt_mac(self):
        mds_net = bottle.request.app.config['dnsmasq.net_name']
        dnsmasq_base = bottle.request.app.config['dnsmasq.base_dir']
        # the leases/mac/whatever file is either a <net>.leases file in a
        # simple line-oriented format, or an <interface>.status file
        # in a json format. The interface is configured in the <net>.conf file.
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting MAC for %s" % (client_host))

        try:
            lease_file = os.path.join(dnsmasq_base, mds_net + '.leases')
            logger.debug("Trying leases file: %s", lease_file)
            for line in open(lease_file):
                line_parts = line.split(" ")
                if client_host == line_parts[2]:
                    mac = line_parts[1]
                    logger.debug("Got MAC: %s" % (mac))
                    return mac
        except IOError:
            logger.debug("Trying status file")
            conf_file = os.path.join(dnsmasq_base, mds_net + '.conf')
            interface = None
            for line in open(conf_file):
                line_parts = line.split("=")
                if "interface" == line_parts[0]:
                    interface = line_parts[1].rstrip()
            try:
                lease_file = os.path.join(dnsmasq_base, interface + '.status')
                status = json.load(open(lease_file))
                for host in status:
                    if host['ip-address'] == client_host:
                        logger.debug("Got MAC: %s" % (host['mac-address']))
                        return host['mac-address']
            except IOError as e:
                logger.warning("Error reading lease file: %s" % (e))

    # We have the IP address of the remote host, and we want to convert that
    # into a domain name we can use as a hostname. This needs to go via the MAC
    # address that dnsmasq records for the IP address, since that's the only
    # identifying information we have available.
    def _get_hostname_from_libvirt_domain(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting hostname for %s (libvirt)", client_host)

        mds_net = bottle.request.app.config['dnsmasq.net_name']
        mac_addr = self._get_mgmt_mac()
        mac_domain_mapping = self._get_domain_macs(mds_net)
        domain_name = mac_domain_mapping[mac_addr].name()
        logger.debug("Found hostname for %s: %s" % (client_host, domain_name))
        self._update_dnsmasq(client_host, domain_name)
        return domain_name

    def gen_metadata(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting metadata for %s", client_host)

        res = ["instance-id",
               "hostname",
               "public-keys",
               ""]
        return self.make_content(res)

    # See if we can find a userdata template file (which may be a plain
    # cloud-init config) in the userdata directory. Files are named
    # <userdata_dir>/<domain>, with a fallback to <userdata_dir>/<mac> if the
    # domain file isn't found. Also, since this is almost certainly going to be
    # a cloud-init config we'll search for the same with an appended .yaml
    def _get_userdata_template(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        userdata_dir = bottle.request.app.config['mdserver.userdata_dir']
        hostname = self.gen_hostname().rstrip()
        mac = self._get_mgmt_mac()
        name = os.path.join(userdata_dir, hostname)
        if os.path.exists(name):
            logger.debug("Found userdata for %s at %s" % (client_host, name))
            return open(name).read()
        name = os.path.join(userdata_dir, hostname + ".yaml")
        if os.path.exists(name):
            logger.debug("Found userdata for %s at %s" % (client_host, name))
            return open(name).read()
        name = os.path.join(userdata_dir, mac)
        if os.path.exists(name):
            logger.debug("Found userdata for %s at %s" % (client_host, name))
            return open(name).read()
        name = os.path.join(userdata_dir, mac + ".yaml")
        if os.path.exists(name):
            logger.debug("Found userdata for %s at %s" % (client_host, name))
            return open(name).read()
        return USERDATA_TEMPLATE

    def gen_userdata(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting userdata for %s" % (client_host))

        config = bottle.request.app.config
        _keys = filter(lambda x: x.startswith('public-keys'), config)
        keys = map(lambda x: x.split('.')[1], _keys)
        for key in keys:
            config['public_key_' + key] = config['public-keys.' + key]
        if config['mdserver.password']:
            config['mdserver_password'] = config['mdserver.password']
        config['hostname'] = self.gen_hostname().strip('\n')
        user_data_template = self._get_userdata_template()
        try:
            user_data = template(user_data_template, **config)
        except Exception as e:
            logger.error("Exception %s: template for %s failed?",
                 e,
                 config['hostname'])
        return self.make_content(user_data)

    def gen_hostname_old(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        prefix = bottle.request.app.config['mdserver.hostname_prefix']
        res = "%s-%s" % (prefix, client_host.split('.')[-1])
        self._update_dnsmasq(client_host, res)
        return self.make_content(res)

    def gen_hostname(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting hostname for %s" % (client_host))

        try:
            hostname = self._get_hostname_from_libvirt_domain()
        except Exception as e:
            logger.error("Exception %s: using old hostname", e)
            return self.gen_hostname_old()

        if not hostname:
            return self.gen_hostname_old()
        return hostname

    def gen_public_keys(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting public keys for %s" % (client_host))

        res = bottle.request.app.config.keys()
        _keys = filter(lambda x: x.startswith('public-keys'), res)
        keys = map(lambda x: x.split('.')[1], _keys)
        keys.append("")
        return self.make_content(keys)

    def gen_public_key_dir(self, key):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting public key directory for %s" % (client_host))
        res = ""
        if key in self.gen_public_keys():
            res = "openssh-key"
        return self.make_content(res)

    def gen_public_key_file(self, key='default'):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting public key file for %s" % (client_host))
        if key not in self.gen_public_keys():
            key = 'default'
        res = bottle.request.app.config['public-keys.%s' % key]
        return self.make_content(res)

    def gen_instance_id(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting instance-id for %s" % (client_host))
        iid = "i-%s" % client_host
        return self.make_content(iid)

    def make_content(self, res):
        if isinstance(res, list):
            return "\n".join(res)
        elif isinstance(res, basestring):
            return "%s\n" % res


def main():
    app = bottle.default_app()
    app.config['mdserver.md_base'] = "/2009-04-04"
    app.config['mdserver.password'] = None
    app.config['mdserver.hostname_prefix'] = 'vm'
    app.config['public-keys.default'] = "__NOT_CONFIGURED__"
    app.config['mdserver.port'] = 80
    app.config['mdserver.loglevel'] = 'info'
    app.config['mdserver.userdata_dir'] = '/etc/mdserver/userdata'
    app.config['mdserver.logfile'] = '/var/log/mdserver.log'
    app.config['mdserver.debug'] = 'no'
    app.config['mdserver.listen_address'] = '169.254.169.254'
    app.config['dnsmasq.manage_addnhosts'] = False
    app.config['dnsmasq.base_dir'] = '/var/lib/libvirt/dnsmasq'
    app.config['dnsmasq.net_name'] = 'mds'
    app.config['dnsmasq.prefix'] = 'test-'
    app.config['dnsmasq.domain'] = '.example.com'

    if len(sys.argv) > 1:
        config_file = sys.argv[1]
        print("Loading config file: %s" % config_file)
        if os.path.exists(config_file):
            app.config.load_config(config_file)
        for i in app.config:
            print("%s = %s" % (i, app.config[i]))

    loglevel = getattr(logging, app.config['mdserver.loglevel'].upper())
    # set up the logger
    print("Loglevel: %s" % (loglevel))
    logger.setLevel(loglevel)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s: %(message)s",
        datefmt='%Y-%m-%d %X'
    )

    debug = app.config['mdserver.debug']
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    file_handler = logging.FileHandler(app.config['mdserver.logfile'])
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if strtobool(debug):
        # send output to stdout
        print("Logging to stdout")
        stream_handler.setLevel(logging.DEBUG)

    install(log_to_logger)

    if app.config['public-keys.default'] == "__NOT_CONFIGURED__":
        logger.info("============Default public key not set !!!=============")

    mdh = MetadataHandler()

    manage_addnhosts = app.config['dnsmasq.manage_addnhosts']
    if manage_addnhosts != False and strtobool(manage_addnhosts):
        mdh._set_dnsmasq_handler(
            Dnsmasq(os.path.join(
                app.config['dnsmasq.base_dir'],
                app.config['dnsmasq.net_name'] + '.conf'
                )
            )
        )

    route(app.config['mdserver.md_base'] + '/meta-data/',
          'GET', mdh.gen_metadata)
    route(app.config['mdserver.md_base'] + '/user-data',
          'GET', mdh.gen_userdata)
    route(app.config['mdserver.md_base'] + '/meta-data/hostname',
          'GET', mdh.gen_hostname)
    route(app.config['mdserver.md_base'] + '/meta-data/instance-id',
          'GET', mdh.gen_instance_id)
    route(app.config['mdserver.md_base'] + '/meta-data/public-keys',
          'GET', mdh.gen_public_keys)
    route(app.config['mdserver.md_base'] + '/meta-data/public-keys/',
          'GET', mdh.gen_public_keys)
    route(app.config['mdserver.md_base'] + '/meta-data/<key>',
          'GET', mdh.gen_public_key_dir)
    route(app.config['mdserver.md_base'] + '/meta-data/<key>/',
          'GET', mdh.gen_public_key_dir)
    route(app.config['mdserver.md_base'] + '/meta-data/<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route(app.config['mdserver.md_base'] + '/meta-data//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    route((app.config['mdserver.md_base'] +
          '/meta-data/public-keys//<key>/openssh-key'),
          'GET', mdh.gen_public_key_file)
    route('/latest' + '/meta-data/public-keys//<key>/openssh-key',
          'GET', mdh.gen_public_key_file)
    svr_port = app.config.get('mdserver.port')
    listen_addr = app.config.get('mdserver.listen_address')
    run(host=listen_addr, port=svr_port)

if __name__ == '__main__':
    main()
