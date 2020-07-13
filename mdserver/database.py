#
# mdserver/database.py: a simple single-file database of domain information
# used to manage data for the md_server system.
#
# Data is stored on disk in a simple JSON format, with data being stored as a
# list of JSON objects, each specifying domain information. In memory the data
# is stored as dict keyed by the domain name.
#
# Read operations are performed by reading the whole file, parsing the JSON,
# then creating the domain name keyed dict, along with additional indices to
# asist with queries.
#
# Update operations do the same loading and processing, but then update the
# core dict with new data. The whole core dict is then serialised back to JSON
# and written to a temp file, which is then renamed over the original file to
# complete the process.
#
# JSON format is as follows:
# [
#   {
#     "domain_name": "foo",
#     "domain_uuid": "xx-xx-xx-xx-xx",
#     "mds_mac": "00:11:22:33:44:55",
#     "mds_ipv4": "10.128.0.2",
#     "mds_ipv6": "fe80::128:2",
#     "first_seen": 123456789,
#     "last_update": 123456799
#   }
# ]

import ipaddress
import json
import os
import time


class Database(object):
    """A simple database of host and lease information.

    Objects created by this class contain a Python dict storing data from a
    list of JSON objects loaded from an on-disk database file. Database objects
    are inherently ephemeral - they are created at the start of a transaction
    and are dropped at the end of the transaction, having either been queried
    or updated and stored back to disk.
    """

    # anything other than these keys are considered transient and are not
    # indexed
    index_keys = [
        'domain_name',
        'domain_uuid',
        'mds_mac',
        'mds_ipv4',
        'mds_ipv6',
    ]

    def __init__(self, dbfile):
        """Create a new in-memory database, loading the data from the specified
        database file.
        """
        self.dbfile = dbfile
        try:
            with open(dbfile, "r") as dbf:
                self.db_core = json.load(dbf)
        except FileNotFoundError:
            self.db_core = []
        self._create_indices()

    def _create_indices(self):
        self.indices = {}
        for key in self.index_keys:
            self.indices[key] = {e[key]: e for e in self.db_core}

    @classmethod
    def new_entry(cls):
        """Return an empty database entry.
        """
        return {
            'domain_name': None,
            'domain_uuid': None,
            'mds_mac': None,
            'mds_ipv4': None,
            'mds_ipv6': None,
            'first_seen': None,
            'last_update': None,
        }

    @classmethod
    def _check_entry(cls, entry):
        """Verify that the supplied entry is the correct format.
        """
        keys = list(cls.new_entry().keys())
        for key in keys:
            if key not in entry:
                raise ValueError("Entry missing key %s" % (key))
        for key in entry:
            if key not in keys:
                raise ValueError("Unknown entry key %s" % (key))

    def store(self, dbfile=None):
        """Store the current state of the database to disk.
        """
        if not dbfile:
            dbfile = self.dbfile
        dbtext = json.dumps(self.db_core, indent=4)
        tmpfile = dbfile + '.tmp'
        with open(tmpfile, 'w') as dbf:
            dbf.write(dbtext)
        os.rename(tmpfile, dbfile)
        print(dbtext)

    def add_or_update_entry(self, entry):
        """Add an entry to the database, or update when an existing entry is
        found. Returns the current state of the entry.

        Updates an existing entry with the same domain_name value. During an
        update any None elements in the new entry will preserve the existing
        value for that element in the database.

        Will never update the 'first_seen' value.
        """
        self._check_entry(entry)
        if entry['domain_name'] in self.indices['domain_name']:
            oe = self.indices['domain_name'][entry['domain_name']]
            for key in entry:
                if entry[key] is not None and key != 'first_seen':
                    oe[key] = entry[key]
        else:
            entry['first_seen'] = time.time()
            self.db_core.append(entry)
        self._create_indices()
        return self.query('domain_name', entry['domain_name'])

    def del_entry(self, entry):
        """Remove an entry from the database.
        """
        pass

    def query(self, key, needle):
        """Search the database.

        Queries are made by specifying a key and a search term. The key is one
        of the database key values, and the search term is a simple string
        value to be searched for.
        """
        if key not in self.indices:
            raise ValueError("%s is not a valid database key" % (key))
        try:
            return self.indices[key][needle]
        except KeyError:
            return None

    def gen_ip(self, network, prefix, exclude=[]):
        """Generate a new IP address within the specified network, excluding
        addresses from the specified exclude list. Addresses will be guaranteed
        not to exist in the current database.
        """
        version_keys = {
            4: 'mds_ipv4',
            6: 'mds_ipv6',
        }
        exclude_map = {e: e for e in exclude}
        net = ipaddress.ip_network("%s/%s" % (network, prefix))
        for address in net.hosts():
            print("Add", str(address), "Exc", exclude_map)
            # test against the exclude list
            if str(address) in exclude_map:
                continue
            # then test against the database
            if self.query(version_keys[address.version], str(address)):
                continue
            return str(address)
        return None

    def __iter__(self):
        return self.db_core.__iter__()
