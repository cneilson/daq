"""Networking module"""

import logging
import os
import time

from mininet import node as mininet_node
from mininet import net as mininet_net
from mininet import link as mininet_link
from mininet import cli as mininet_cli
from clib import mininet_test_topo

LOGGER = logging.getLogger('network')

class DAQHost(mininet_test_topo.FaucetHostCleanup, mininet_node.Host):
    """Base Mininet Host class, for Mininet-based tests."""
    # pylint: disable=too-few-public-methods
    pass


class DummyNode(object):
    """Dummy node used to handle shadow devices"""
    # pylint: disable=invalid-name
    def addIntf(self, node, port=None):
        """No-op for adding an interface"""
        pass

    def cmd(self, cmd, *args, **kwargs):
        """No-op for running a command"""
        pass


class TestNetwork(object):
    """Test network manager"""

    OVS_CLS = mininet_node.OVSSwitch

    def __init__(self, config):
        self.config = config
        self.net = None
        self.pri = None
        self.sec = None
        self.sec_dpid = None
        self.sec_port = None
        self.sec_name = None
        self.switch_links = {}
        self.device_intfs = None

    # pylint: disable=too-many-arguments
    def add_host(self, name, cls=DAQHost, ip_addr=None, env_vars=None, vol_maps=None,
                 port=None, tmpdir=None):
        """Add a host to the ecosystem"""
        params = {'ip': ip_addr} if ip_addr else {}
        params['tmpdir'] = os.path.join(tmpdir, 'nodes') if tmpdir else None
        params['env_vars'] = env_vars if env_vars else []
        params['vol_maps'] = vol_maps if vol_maps else []
        host = self.net.addHost(name, cls, **params)
        try:
            LOGGER.debug('Created host %s with pid %s/%s', name, host.pid, host.shell.pid)
            switch_link = self.net.addLink(self.pri, host, port1=port, fast=False)
            self.switch_links[host] = switch_link
            if self.net.built:
                host.configDefault()
                self._switch_attach(self.pri, switch_link.intf1)
        except:
            host.terminate()
            raise
        return host

    def get_host_interface(self, host):
        """Get the internal link interface for this host"""
        return self.switch_links[host].intf2

    def _switch_attach(self, switch, intf):
        switch.attach(intf)
        # This really should be done in attach, but currently only automatic on switch startup.
        switch.vsctl(switch.intfOpts(intf))

    def _switch_del_intf(self, switch, intf):
        del switch.intfs[switch.ports[intf]]
        del switch.ports[intf]
        del switch.nameToIntf[intf.name]

    def remove_host(self, host):
        """Remove a host from the ecosystem"""
        index = self.net.hosts.index(host)
        if index:
            del self.net.hosts[index]
        if host in self.switch_links:
            switch_link = self.switch_links[host]
            del self.switch_links[host]
            intf = switch_link.intf1
            self.pri.detach(intf)
            self._switch_del_intf(self.pri, intf)
            intf.delete()
            del self.net.links[self.net.links.index(switch_link)]

    def _make_device_intfs(self):
        intf_names = self.config['daq_intf'].split(',')
        intfs = []
        for intf_name in intf_names:
            intf_name = intf_name[0:-1] if intf_name.endswith('!') else intf_name
            port_no = len(intfs) + 1
            intf = mininet_link.Intf(intf_name.strip(), node=DummyNode(), port=port_no)
            intf.port = port_no
            intfs.append(intf)
        return intfs

    def flap_interface_ports(self):
        """Flap all interface ports to trigger start-up behavior"""
        if self.device_intfs:
            for device_intf in self.device_intfs:
                self._flap_interface_port(device_intf.name)

    def _flap_interface_port(self, intf_name):
        if intf_name.startswith('faux') or intf_name == 'local':
            LOGGER.info('Flapping device interface %s.', intf_name)
            self.sec.cmd('ip link set %s down' % intf_name)
            time.sleep(0.5)
            self.sec.cmd('ip link set %s up' % intf_name)

    def _create_secondary(self):
        self.sec_port = int(self.config['ext_port'] if 'ext_port' in self.config else 47)
        if 'ext_dpid' in self.config:
            self.sec_dpid = int(self.config['ext_dpid'], 0)
            self.sec_name = self.config['ext_intf']
            LOGGER.info('Configuring external secondary with dpid %s on intf %s',
                        self.sec_dpid, self.sec_name)
            sec_intf = mininet_link.Intf(self.sec_name, node=DummyNode(), port=1)
            self.pri.addIntf(sec_intf, port=1)
        else:
            self.sec_dpid = 2
            LOGGER.info('Creating ovs secondary with dpid/port %s/%d',
                        self.sec_dpid, self.sec_port)
            self.sec = self.net.addSwitch('sec', dpid=str(self.sec_dpid), cls=self.OVS_CLS)

            link = self.net.addLink(self.pri, self.sec, port1=1,
                                    port2=self.sec_port, fast=False)
            LOGGER.info('Added switch link %s <-> %s', link.intf1.name, link.intf2.name)
            self.sec_name = link.intf2.name

    def is_device_port(self, dpid, port):
        """Check if the dpid/port combo is for a valid device"""
        target_dpid = int(self.sec_dpid)
        return dpid == target_dpid and port < self.sec_port

    def cmd(self, cmd):
        """Execute the command in the base context"""
        return self.pri.cmd(cmd)

    def cli(self):
        """Drop into the mininet CLI"""
        mininet_cli.CLI(self.net)

    def stop(self):
        """Stop network"""
        self.net.stop()

    def initialize(self):
        """Initialize network"""

        LOGGER.debug("Creating miniet...")
        self.net = mininet_net.Mininet()

        LOGGER.debug("Adding switches...")
        self.pri = self.net.addSwitch('pri', dpid='1', cls=self.OVS_CLS)
        self._create_secondary()

        target_ip = "127.0.0.1"
        LOGGER.debug("Adding controller at %s", target_ip)
        controller = mininet_node.RemoteController
        self.net.addController('controller', controller=controller,
                               ip=target_ip, port=6633)

        LOGGER.info("Starting mininet...")
        self.net.start()

        if self.sec:
            self.device_intfs = self._make_device_intfs()
            for device_intf in self.device_intfs:
                LOGGER.info("Attaching device interface %s on port %d.",
                            device_intf.name, device_intf.port)
                self.sec.addIntf(device_intf, port=device_intf.port)
                self._switch_attach(self.sec, device_intf)
