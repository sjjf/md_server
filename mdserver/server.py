#
# Copyright 2016-2019 Australian National University
#
# Please see the LICENSE.txt file for details.

import bottle
import logging
import os
import sys
import time

from bottle import abort, route, run, template, request, response, install
from datetime import datetime
from distutils.util import strtobool
from functools import wraps
from mdserver.database import Database
from mdserver.dnsmasq import Dnsmasq
from mdserver.libvirt import get_domain_data


VERSION = "0.6.0"


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
        self.default_template = USERDATA_TEMPLATE
        self.public_keys = {}

    def _set_public_keys(self, config):
        # we store the public key name here, and use tht to retrieve the
        # actual key strig from the config when it's required
        keys = [k.split('.')[1]
                for k in config.keys()
                if k.startswith('public-keys')]
        for i, k in enumerate(keys):
            self.public_keys[i] = k

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

    def _get_mgmt_mac(self, client_name):
        logger.debug("Getting MAC for %s", client_name)
        config = bottle.request.app.config
        db = Database(config['mdserver.db_file'])
        entry = db.query('mds_ipv4', client_name)
        if entry is None:
            logger.debug("Failed to find MAC for %s in database",
                         client_name)
            raise ValueError("No lease for %s?" % (client_name))
        return entry['mds_mac']

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
            mac = self._get_mgmt_mac(client_host)
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
        return self.default_template

    def _get_template_data(self, config):
        keys = [k.split('.')[1]
                for k in config.keys()
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
        return self.make_content(user_data)

    def gen_hostname(self):
        client_ip = bottle.request.get('REMOTE_ADDR')
        logger.debug("Getting hostname for %s", client_ip)
        config = bottle.request.app.config
        db = Database(config['mdserver.db_file'])
        entry = db.query('mds_ipv4', client_ip)
        if entry is None:
            logger.info("Failed to find MAC for %s in database",
                        client_ip)
            abort(401, "Unknown client")
        return entry['domain_name']

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
        if int(key) in self.public_keys:
            key = self.public_keys[int(key)]
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
        # note that we only test against str here - this excludes unicode
        # strings on python2, but nothing we're doing here should be unicode
        # so we can safely ignore that
        if isinstance(res, list):
            return "\n".join(res)
        elif isinstance(res, str):
            return "%s" % res

    def _get_ec2_versions(self, config):
        vraw = config['service.ec2_versions'].split(',')
        versions = [v.strip() for v in vraw if len(v.strip()) > 0]
        return versions

    def instance_upload(self):
        client_host = bottle.request.get('REMOTE_ADDR')
        config = bottle.request.app.config
        # for whatever reason, the source address ends up being the same as
        # the listen address when connections are coming from localhost
        if client_host != config['mdserver.listen_address']:
            abort(401, "access denied")
        data = bottle.request.body.getvalue()
        logger.debug("Got instance upload with data %s", data[0:25])
        dbentry = get_domain_data(data, config['dnsmasq.net_name'])
        dbentry['last_update'] = time.time()
        db = Database(config['mdserver.db_file'])
        entry = db.add_or_update_entry(dbentry)
        if entry['mds_ipv4'] is None:
            entry['mds_ipv4'] = db.gen_ip(
                config['dnsmasq.net_address'],
                config['dnsmasq.net_prefix'],
                exclude=[config['dnsmasq.gateway']]
            )
            if entry['mds_ipv4'] is None:
                logger.warning(
                    "Failed to allocate address for %s",
                    entry['domain_name']
                )
            db.add_or_update_entry(entry)
        db.store()
        dnsmasq = Dnsmasq(config)
        dnsmasq.gen_dhcp_hosts(db)
        dnsmasq.gen_dns_hosts(db)


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
    app.config['mdserver.db_file'] = '/var/lib/mdserver/db_file.json'
    app.config['dnsmasq.user'] = 'mdserver'
    app.config['dnsmasq.base_dir'] = '/var/lib/mdserver/dnsmasq'
    app.config['dnsmasq.run_dir'] = '/var/run/mdserver'
    app.config['dnsmasq.net_name'] = 'mds'
    app.config['dnsmasq.net_address'] = '10.122.0.0'
    app.config['dnsmasq.net_prefix'] = '16'
    app.config['dnsmasq.gateway'] = '10.122.0.1'
    app.config['dnsmasq.use_dns'] = False
    app.config['dnsmasq.interface'] = 'br-mds'
    app.config['dnsmasq.lease_len'] = 86400
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

    db = Database(app.config['mdserver.db_file'])
    dnsmasq = Dnsmasq(app.config)
    dnsmasq.gen_dnsmasq_config()
    dnsmasq.gen_dhcp_hosts(db)
    dnsmasq.gen_dns_hosts(db)

    if app.config['public-keys.default'] == "__NOT_CONFIGURED__":
        logger.info("============Default public key not set !!!=============")

    mdh = MetadataHandler()

    if app.config['mdserver.default_template']:
        mdh._set_default_template(app.config['mdserver.default_template'])

    # sanitise prefix and domain strings
    prefix = app.config['dnsmasq.prefix']
    app.config['dnsmasq.prefix'] = strtobool_or_val(prefix)
    domain = app.config['dnsmasq.domain']
    app.config['dnsmasq.domain'] = strtobool_or_val(domain)

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

    # support for uploading instance data
    route('/instance-upload', 'POST', mdh.instance_upload)

    svr_port = app.config.get('mdserver.port')
    listen_addr = app.config.get('mdserver.listen_address')
    run(host=listen_addr, port=svr_port)


if __name__ == '__main__':
    main()
