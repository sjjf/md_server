#
# mdserver/config.py: manage md_server configuration.
#
# We use the in-built Bottle configuration mechanism (i.e. the `app.config`
# ConfigDict object created by default), setting defaults, then where a
# config file has been specified loading that using the `ConfigDict.load_file`
# method.
#
# We support loading additional files (and files from a directory), with files
# or directories to load specified in the `[_include_]` section of the main
# config file.
#

import os

from mdserver.version import VERSION


def set_defaults(app):
    """Set the hard-coded configuration defaults for app.

    These defaults should work for very basic cases, but really need to be
    modified for a real-world deployment.
    """
    app.config["service.name"] = "mdserver"
    app.config["service.type"] = "mdserver"
    app.config["service.version"] = VERSION
    app.config["service.ec2_versions"] = "2009-04-04"
    app.config["mdserver.password"] = None
    app.config["mdserver.hostname_prefix"] = "vm"
    app.config["public-keys.default"] = "__NOT_CONFIGURED__"
    app.config["mdserver.port"] = 80
    app.config["mdserver.loglevel"] = "info"
    app.config["mdserver.userdata_dir"] = "/etc/mdserver/userdata"
    app.config["mdserver.logfile"] = "/var/log/mdserver.log"
    app.config["mdserver.debug"] = "no"
    app.config["mdserver.listen_address"] = "169.254.169.254"
    app.config["mdserver.default_template"] = None
    app.config["mdserver.db_file"] = "/var/lib/mdserver/db_file.json"
    app.config["dnsmasq.user"] = "mdserver"
    app.config["dnsmasq.base_dir"] = "/var/lib/mdserver/dnsmasq"
    app.config["dnsmasq.run_dir"] = "/var/run/mdserver"
    app.config["dnsmasq.net_name"] = "mds"
    app.config["dnsmasq.net_address"] = "10.122.0.0"
    app.config["dnsmasq.net_prefix"] = "16"
    app.config["dnsmasq.gateway"] = "10.122.0.1"
    app.config["dnsmasq.use_dns"] = False
    app.config["dnsmasq.interface"] = "br-mds"
    app.config["dnsmasq.lease_len"] = 86400
    app.config["dnsmasq.prefix"] = False
    app.config["dnsmasq.domain"] = False
    app.config["dnsmasq.entry_order"] = "base"


def log(app, logger):
    """Log the contents of ConfigDict."""
    for i in app.config:
        logger.debug("%s = %s", i, app.config[i])


def load_dir(app, dirname):
    """Load the contents of all `*.conf` files found in the given directory, in
    lexical order.
    """
    print("Loading files from %s" % (dirname))
    files = []
    with os.scandir(dirname) as dir:
        for entry in dir:
            if (
                entry.is_file()
                and entry.name.endswith(".conf")
                and not entry.name.startswith(".")
            ):
                files.append(entry.name)

    files.sort()
    abs_files = [os.path.join(dirname, fname) for fname in files]
    for f in abs_files:
        print("Loading config from %s" % (f))
        app.config.load_config(f)
    app.config["_files_." + dirname] = ",".join(files)


def load_files(app, files):
    """Load the contents of the specified files."""
    for f in files:
        app.config.load_config(f)
    app.config["_files_.files"] = ",".join(files)


def load(app, filename):
    """Load configuration into app.config from the specified file.

    File is a standard ini style config file.

    Additional files and/or directories can be included via the `_include_`
    section, with a colon-separated list of files or paths specified via the
    `files` and `directories` keys, with directories read first followed by
    individually specified files. Files are read in the order specified, left
    to right; directories are searched in the order specified, left to right,
    with all `.conf` files in each directory read in lexical order. The last
    version of a particular section/key found wins.
    """

    conf_dirs = []
    conf_files = []
    if os.path.exists(filename):
        app.config.load_config(filename)
        app.config["_files_.main"] = filename
        if "_include_.directories" in app.config:
            dirlist = app.config["_include_.directories"]
            conf_dirs = [
                os.path.abspath(d) for d in dirlist.split(":") if os.path.isdir(d)
            ]
        if "_include_.files" in app.config:
            filelist = app.config["_include_.files"]
            conf_files = [
                os.path.abspath(f) for f in filelist.split(":") if os.path.isfile(f)
            ]
        for conf_dir in conf_dirs:
            load_dir(app, conf_dir)
        load_files(app, conf_files)
