#
# Copyright 2016-2019 Australian National University
#
# Please see the LICENSE.txt file for details.

import bottle
import json
import libvirt
import logging
import os
import sys
import xmltodict

from bottle import abort, route, run, template, request, response, install
from datetime import datetime
from distutils.util import strtobool
from dnsmasq.dnsmasq import Dnsmasq
from functools import wraps


VERSION = "0.5.0"


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
        logger.info('%s %s %s %s %s',
                    request.remote_addr,
                    request_time,
                    request.method,
                    request.url,
                    response.status)
        return actual_response
    return _log_to_logger


def strtobool_or_val(string):
    """Return a boolean True/False if string is or parses as a boolean,
    otherwise return the string itself.
    """
    if isinstance(string, bool):
        return string
    try:
        return strtobool(string)
    except ValueError:
        return string


class ConfigError(Exception):
    def __init__(self, message):
        self.message = message


class MetadataHandler(object):

    def __init__(self):
        self.dnsmasq = None
        self.default_template = USERDATA_TEMPLATE
        self.public_keys = {}

    def _set_public_keys(self, config):
        # we store the public key name here, and use that to retrieve they
        # actual key string from the config when it's requested
        keys = [k.split('.')[1]
                for k in config.keys()
                if k.startswith('public-keys')]
        for i, k in enumerate(keys):
            self.public_keys[i] = k

    def _set_dnsmasq_handler(self, dnsmasq):
        self.dnsmasq = dnsmasq

    def _set_default_template(self, template_file):
        try:
            tf = open(template_file, 'r')
            self.default_template = tf.read()
            tf.close()
        except IOError:
            logger.error(
                "Default template file specified (%s), but file not found!",
                template_file
            )

    def _update_dnsmasq(self, ip, name):
        """Update the dnsmasq additional hosts file."""
        if not self.dnsmasq:
            return
        config = bottle.request.app.config
        # clear out any existing entries for this name
        ips = self.dnsmasq.get_addn_host_by_name(name)
        if len(ips) > 0:
            for oip in ips:
                if oip != ip:
                    self.dnsmasq.del_addn_host(oip)
        # and add our new entry
        # the entry_order value specifies the order in which the
        # basename, prefixed and fqdn entries are added.
        entry_order = config['dnsmasq.entry_order']
        entry_order = [e.strip().lower() for e in entry_order.split(',')]
        prefix = strtobool_or_val(config['dnsmasq.prefix'])
        domain = strtobool_or_val(config['dnsmasq.domain'])
        # if prefix is disabled, the fqdn should use the basename instead
        prefixed = name
        if prefix:
            prefixed = prefix + name
        fqdn = prefixed + '.' + domain
        names = []
        for entry in entry_order:
            if entry.startswith('base'):
                names.append(name)
            elif entry.startswith('prefix'):
                if prefix:
                    names.append(prefixed)
            elif entry == 'domain' or entry == 'fqdn':
                if domain:
                    names.append(fqdn)
        if len(names) > 0:
            self.dnsmasq.set_addn_host(ip, names)
        else:
            # if we end up with no names, we want to make sure that the
            # current entry is gone rather than leave an old stale entry
            self.dnsmasq.del_addn_host(ip)

    def _get_all_domains(self):
        conn = libvirt.open()
        return conn.listAllDomains(0)

    # filters work by specifying a tag, and a set of attributes on that tag
    # which need to be matched: {'tag': 'source', 'attrs': {'network': 'mds'}}
    # matches source tags that have the network attribute set to 'mds'. Only a
    # single tag is supported, but potentially more than one attribute. An
    # empty filter means return all interfaces
    def _get_domain_interfaces(self, domain, filter={}):
        dom = xmltodict.parse(domain.XMLDesc(0))
        interfaces = dom['domain']['devices']['interface']
        # if there's just the one interface xmltodict doesn't create a list
        # with a single entry, so we need to do that here
        if type(interfaces) != list:
            interfaces = [interfaces]
        try:
            tag = filter['tag']
            attrs = filter['attrs']
        except KeyError:
            return interfaces

        # since we only have a single tag to search for, we just iterate over
        # each interface looking for one that has the tag, then search under
        # the tag for the attributes we care about. xmltodict adds attributes
        # to its output by prepending the attribute name with an '@', then
        # adding the @attribute to the dict same as any other tags. this is a
        # little unwiedly, but not complicated to deal with.
        #
        # XXX: this is probably overly complicated - it's not like we really
        # /need/ this level of generality . . .
        logger.debug(
            "Searching domain %s interfaces by tag %s",
            dom['domain']['name'],
            tag
        )
        accum = []
        for interface in interfaces:
            if tag in interface:
                require = len(attrs.keys())
                for attr in attrs.keys():
                    atat = "@{}".format(attr)
                    if atat in interface[tag]:
                        if interface[tag][atat] == attrs[attr]:
                            logger.debug(
                                "Matched - domain %s has attr %s (value %s)",
                                dom['domain']['name'],
                                attr, attrs[attr]
                            )
                            require -= 1
                if require == 0:
                    accum.append(interface)
        return accum

    def _get_mac_from_interface(self, interface):
        if 'mac' in interface:
            if '@address' in interface['mac']:
                return interface['mac']['@address']

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
            with open(lease_file) as leases:
                for line in leases.readlines():
                    line_parts = line.split(" ")
                    if client_host == line_parts[2]:
                        mac = line_parts[1]
                        logger.debug("Got MAC: %s" % (mac))
                        return mac
                logger.debug(
                    ("Failed to get MAC for %s - trying status file "
                     "(possible stale leases file)"),
                    client_host)
                raise ValueError("No lease for %s?" % (client_host))
        except (IOError, ValueError):
            logger.debug("Trying status file")
            conf_file = os.path.join(dnsmasq_base, mds_net + '.conf')
            interface = None
            try:
                with open(conf_file) as conf:
                    for line in conf.readlines():
                        line_parts = line.split("=")
                        if "interface" == line_parts[0]:
                            interface = line_parts[1].rstrip()
                try:
                    lease_file = os.path.join(dnsmasq_base,
                                              interface + '.status')
                    with open(lease_file) as leases:
                        status = json.load(leases)
                        for host in status:
                            if host['ip-address'] == client_host:
                                logger.debug("Host %s has MAC %s",
                                             host['ip-address'],
                                             host['mac-address'])
                                return host['mac-address']
                    logger.debug("Failed to get mac for %s", client_host)
                    raise ValueError("No lease for %s?" % (client_host))
                except IOError as e:
                    logger.warning("Error reading lease file: %s", e)
            except IOError as e:
                # log then re-raise
                logger.error("Error reading dnsmasq config file %s: %s",
                             mds_net + '.conf', e)
                raise IOError(e)

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
        logger.debug("Found hostname for %s: %s", client_host, domain_name)
        self._update_dnsmasq(client_host, domain_name)
        return domain_name

    def gen_versions(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting versions for %s", client_host)
        config = bottle.request.app.config
        versions = [v + "/" for v in self._get_ec2_versions(config)]
        return self.make_content(versions)

    def gen_base(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting base for %s", client_host)

        return self.make_content([
            "meta-data/",
            "user-data"
        ])

    def gen_metadata(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting metadata for %s", client_host)

        return self.make_content([
            "instance-id",
            "hostname",
            "public-keys/"
        ])

    # See if we can find a userdata template file (which may be a plain
    # cloud-init config) in the userdata directory. Files are named
    # <userdata_dir>/<domain>, with a fallback to <userdata_dir>/<mac> if the
    # domain file isn't found. Also, since this is almost certainly going to be
    # a cloud-init config we'll search for the same with an appended .yaml
    def _get_userdata_template(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        userdata_dir = bottle.request.app.config['mdserver.userdata_dir']
        hostname = self.gen_hostname().rstrip()
        name = os.path.join(userdata_dir, hostname)
        if os.path.exists(name):
            logger.debug("Found userdata for %s at %s", client_host, name)
            return open(name).read()
        name = os.path.join(userdata_dir, hostname + ".yaml")
        if os.path.exists(name):
            logger.debug("Found userdata for %s at %s", client_host, name)
            return open(name).read()
        try:
            mac = self._get_mgmt_mac()
            name = os.path.join(userdata_dir, mac)
            if os.path.exists(name):
                logger.debug("Found userdata for %s at %s", client_host, name)
                return open(name).read()
            name = os.path.join(userdata_dir, mac + ".yaml")
            if os.path.exists(name):
                logger.debug("Found userdata for %s at %s", client_host, name)
                return open(name).read()
        except IOError as e:
            logger.debug("IOError trying to find userdata by MAC: %s", repr(e))
        logger.debug("Using default userdata template for %s", client_host)
        return self.default_template

    def _get_template_data(self, config):
        keys = [k.split('.')[1]
                for k, v in config.items()
                if k.startswith('template-data')]
        # make sure that we can't overwrite a core config element
        for key in keys:
            if key not in config:
                config[key] = config['template-data.' + key]
        return config

    def _get_public_keys(self, config):
        keys = [k.split('.')[1]
                for k in config.keys()
                if k.startswith('public-keys')]
        for key in keys:
            config['public_key_' + key] = config['public-keys.' + key]
        return config

    def gen_userdata(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting userdata for %s", client_host)

        config = bottle.request.app.config
        config = self._get_public_keys(config)
        config = self._get_template_data(config)
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
        logger.debug("Returning userdata %s", user_data[0:25])
        return self.make_content(user_data)

    def gen_hostname_old(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        prefix = bottle.request.app.config['mdserver.hostname_prefix']
        res = "%s-%s" % (prefix, client_host.split('.')[-1])
        self._update_dnsmasq(client_host, res)
        return self.make_content(res)

    def gen_hostname(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting hostname for %s", client_host)

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
        logger.debug("Getting public keys for %s", client_host)

        keys = ["{}={}".format(i, k) for i, k in self.public_keys.items()]
        return self.make_content(keys)

    def gen_public_key_dir(self, key):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting public key directory for %s", client_host)
        res = ""
        if int(key) in self.public_keys:
            res = "openssh-key"
        elif key in self.public_keys.values():
            # technically this shouldn't work, but it doesn't hurt, I think
            res = "openssh-key"
        else:
            abort(404, 'Not found')
        return self.make_content(res)

    def gen_public_key_file(self, key='default'):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting public key file for %s", client_host)
        # if we have one of the key indices, map it to a key name, otherwise
        # just look for the key by name
        try:
            if int(key) in self.public_keys:
                key = self.public_keys[int(key)]
        except ValueError:
            pass
        res = bottle.request.app.config['public-keys.%s' % key]
        return self.make_content(res)

    def gen_instance_id(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting instance-id for %s", client_host)
        iid = "i-%s" % client_host
        return self.make_content(iid)

    def gen_service_info(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting service info for %s", client_host)
        return self.make_content([
            'name',
            'type',
            'version',
            'ec2_versions',
        ])

    def gen_service_name(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting service name for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(config['service.name'])

    def gen_service_type(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting service type for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(config['service.type'])

    def gen_service_version(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting service version for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(config['service.version'])

    def gen_ec2_versions(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting EC2 versions for %s", client_host)
        config = bottle.request.app.config
        return self.make_content(self._get_ec2_versions(config))

    def make_content(self, res):
        # note that we only test against str - this excludes unicode strings
        # on python2, but nothing we're doing here should be unicode so we can
        # safely ignore that
        if isinstance(res, list):
            return "\n".join(res)
        elif isinstance(res, str):
            return "%s" % res

    def _get_ec2_versions(self, config):
        vraw = config['service.ec2_versions'].split(',')
        versions = []
        for v in [v.lstrip().rstrip() for v in vraw]:
            if len(v) > 0:
                versions.append(v)
        return versions


def main():
    app = bottle.default_app()
    app.config['service.name'] = "mdserver"
    app.config['service.type'] = "mdserver"
    app.config['service.version'] = VERSION
    app.config['service.ec2_versions'] = "2009-04-04"
    app.config['mdserver.password'] = None
    app.config['mdserver.hostname_prefix'] = 'vm'
    app.config['public-keys.default'] = "__NOT_CONFIGURED__"
    app.config['mdserver.port'] = 80
    app.config['mdserver.loglevel'] = 'info'
    app.config['mdserver.userdata_dir'] = '/etc/mdserver/userdata'
    app.config['mdserver.logfile'] = '/var/log/mdserver.log'
    app.config['mdserver.debug'] = 'no'
    app.config['mdserver.listen_address'] = '169.254.169.254'
    app.config['mdserver.default_template'] = None
    app.config['dnsmasq.manage_addnhosts'] = False
    app.config['dnsmasq.base_dir'] = '/var/lib/libvirt/dnsmasq'
    app.config['dnsmasq.net_name'] = 'mds'
    app.config['dnsmasq.prefix'] = False
    app.config['dnsmasq.domain'] = False
    app.config['dnsmasq.entry_order'] = 'base'

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

    if app.config['mdserver.default_template']:
        mdh._set_default_template(app.config['mdserver.default_template'])

    manage_addnhosts = app.config['dnsmasq.manage_addnhosts']
    if manage_addnhosts is not False and strtobool(manage_addnhosts):
        mdh._set_dnsmasq_handler(
            Dnsmasq(
                os.path.join(
                    app.config['dnsmasq.base_dir'],
                    app.config['dnsmasq.net_name'] + '.conf'
                )
            )
        )

    mdh._set_public_keys(app.config)

    route('/', 'GET', mdh.gen_versions)
    route('/service/', 'GET', mdh.gen_service_info)
    route('/service/name', 'GET', mdh.gen_service_name)
    route('/service/type', 'GET', mdh.gen_service_type)
    route('/service/version', 'GET', mdh.gen_service_version)
    route('/service/ec2_versions', 'GET', mdh.gen_ec2_versions)

    for md_base in mdh._get_ec2_versions(app.config):
        # skip empty strings - it makes no sense to put metadata directly
        # under /
        if len(md_base) == 0:
            continue
        # make sure the path is always properly rooted
        if len(md_base) > 0 and md_base[0] != '/':
            md_base = '/' + md_base
        route(md_base + '/', 'GET', mdh.gen_base)
        route(md_base + '/meta-data/', 'GET', mdh.gen_metadata)
        route(md_base + '/user-data', 'GET', mdh.gen_userdata)
        route(md_base + '/meta-data/hostname', 'GET', mdh.gen_hostname)
        route(md_base + '/meta-data/instance-id', 'GET', mdh.gen_instance_id)
        route(md_base + '/meta-data/public-keys/', 'GET', mdh.gen_public_keys)
        route(md_base + '/meta-data/public-keys/<key>/', 'GET',
              mdh.gen_public_key_dir)
        route((md_base + '/meta-data/public-keys/<key>/openssh-key'), 'GET',
              mdh.gen_public_key_file)

    svr_port = app.config.get('mdserver.port')
    listen_addr = app.config.get('mdserver.listen_address')
    run(host=listen_addr, port=svr_port)


if __name__ == '__main__':
    main()
