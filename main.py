"""Main module of amlight/coloring Kytos Network Application.

NApp to color a network topology
"""
# Check for import order disabled in pylint due to conflict
# with isort.
# pylint: disable=wrong-import-order
import struct

import requests
from flask import jsonify
from kytos.core import KytosNApp, log, rest
from kytos.core.helpers import listen_to
from napps.amlight.coloring import settings
from pyof.v0x01.common.phy_port import Port
from pyof.v0x04.common.port import PortNo


class Main(KytosNApp):
    """Main class of amlight/coloring NApp.

    This class is the entry point for this napp.
    """

    def setup(self):
        """Replace the '__init__' method for the KytosNApp subclass.

        The setup method is automatically called by the controller when your
        application is loaded.

        So, if you have any setup routine, insert it here.
        """
        self.switches = {}
        self.execute_as_loop(1)

    def execute(self):
        """ Topology updates are executed through events. """

    @listen_to('kytos/topology.updated')
    def topology_updated(self, event):
        """Update colors on topology update."""
        topology = event.content['topology']
        self.update_colors(
            [link.as_dict() for link in topology.links.values()]
        )

    def update_colors(self, links):
        """ Color each switch, with the color based on the switch's DPID.
            After that, if not yet installed, installs, for each switch, flows
            with the color of its neighbors, to send probe packets to the
            controller.
        """
        url = settings.FLOW_MANAGER_URL

        for switch in self.controller.switches.values():
            if switch.dpid not in self.switches:
                color = int(switch.dpid.replace(':', '')[4:], 16)
                self.switches[switch.dpid] = {'color': color,
                                              'neighbors': set(),
                                              'flows': {}}
            else:
                self.switches[switch.dpid]['neighbors'] = set()

        for link in links:
            source = link['endpoint_a']['switch']
            target = link['endpoint_b']['switch']
            if source != target:
                self.switches[source]['neighbors'].add(target)
                self.switches[target]['neighbors'].add(source)

        # Create the flows for each neighbor of each switch and installs it
        # if not already installed
        for dpid, switch_dict in self.switches.items():
            switch = self.controller.get_switch_by_dpid(dpid)
            if switch.ofp_version == '0x01':
                controller_port = Port.OFPP_CONTROLLER
            elif switch.ofp_version == '0x04':
                controller_port = PortNo.OFPP_CONTROLLER
            else:
                continue
            for neighbor in switch_dict['neighbors']:
                if neighbor not in switch_dict['flows']:
                    flow_dict = {
                        'table_id': 0,
                        'match': {},
                        'priority': 50000,
                        'actions': [
                            {'action_type': 'output', 'port': controller_port}
                        ]}

                    flow_dict['match'][settings.COLOR_FIELD] = \
                        self.color_to_field(
                            self.switches[neighbor]['color'],
                            settings.COLOR_FIELD
                        )

                    switch_dict['flows'][neighbor] = flow_dict
                    returned = requests.post(
                        url % dpid,
                        json={'flows': [flow_dict]}
                    )
                    if returned.status_code // 100 != 2:
                        log.error('Flow manager returned an error inserting '
                                  'flow. Status code %s' %
                                  (returned.status_code,))

    def shutdown(self):
        """This method is executed when your napp is unloaded.

        If you have some cleanup procedure, insert it here.
        """

    @staticmethod
    def color_to_field(color, field='dl_src'):
        """
        Gets the color number and returns it in a format suitable for the field
        :param color: The color of the switch (integer)
        :param field: The field that will be used to create the flow for the
        color
        :return: A representation of the color suitable for the given field
        """
        if field in ('dl_src', 'dl_dst'):
            color_48bits = color & 0xffffffffffffffff
            int_mac = struct.pack('!Q', color_48bits)[2:]
            color_value = ':'.join(['%02x' % b for b in int_mac])
            return color_value.replace('00', 'ee')
        if field in ('nw_src', 'nw_dst'):
            color_32bits = color & 0xffffffff
            int_ip = struct.pack('!L', color_32bits)
            return '.'.join(map(str, int_ip))
        if field in ('in_port', 'dl_vlan', 'tp_src', 'tp_dst'):
            return color & 0xffff
        if field in ('nw_tos', 'nw_proto'):
            return color & 0xff
        return color & 0xff

    @rest('colors')
    def rest_colors(self):
        """ List of switch colors."""
        colors = {}
        for dpid, switch_dict in self.switches.items():
            colors[dpid] = {'color_field': settings.COLOR_FIELD,
                            'color_value': self.color_to_field(
                                switch_dict['color'],
                                settings.COLOR_FIELD
                            )}
        return jsonify({'colors': colors})

    @staticmethod
    @rest('/settings', methods=['GET'])
    def return_settings():
        """ List the SDNTrace settings
            Return:
            SETTINGS in JSON format
        """
        settings_dict = dict()
        settings_dict['color_field'] = settings.COLOR_FIELD
        settings_dict['coloring_interval'] = settings.COLORING_INTERVAL
        settings_dict['topology_url'] = settings.TOPOLOGY_URL
        settings_dict['flow_manager_url'] = settings.FLOW_MANAGER_URL
        return jsonify(settings_dict)
