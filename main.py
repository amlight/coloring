"""Main module of amlight/coloring Kytos Network Application.

NApp to color a network topology
"""

from kytos.core import KytosEvent, KytosNApp, log, rest
from kytos.core.helpers import listen_to
from kytos.core.switch import Interface, Switch
from napps.kytos.of_core.v0x01.flow import Flow as Flow10
from napps.kytos.of_core.v0x04.flow import Flow as Flow13
from napps.amlight.coloring import settings
from flask import jsonify
import requests
import json
import struct


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
        self.execute()

    def execute(self):
        """ Get topology through REST on initialization. Topology updates are
            executed through events.
        """
        r = requests.get(settings.TOPOLOGY_URL)
        links = r.json()
        self.update_colors(links['links'])

    @listen_to('kytos.topology.updated')
    def topology_updated(self, event):
        topology = event.content['topology']
        log.info('Updating')
        self.update_colors(
            [{'source': l[0], 'target': l[1]} for l in topology.links]
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
            source = link['source'].split(':')
            target = link['target'].split(':')
            if len(source) < 9 or len(target) < 9:
                continue
            dpid_source = ':'.join(source[:8])
            dpid_target = ':'.join(target[:8])
            self.switches[dpid_source]['neighbors'].add(dpid_target)
            self.switches[dpid_target]['neighbors'].add(dpid_source)

        # Create the flows for each neighbor of each switch and installs it
        # if not already installed
        for dpid, switch_dict in self.switches.items():
            log.debug('DPID: %s, %s' % (dpid, switch_dict))
            for neighbor in switch_dict['neighbors']:
                if neighbor not in switch_dict['flows']:
                    log.info('Neighbor: %s' % neighbor)
                    flow_dict = {
                        'idle_timeout': 0, 'hard_timeout': 0, 'table_id': 0,
                        'buffer_id': None,'match':{
                            'in_port': 0, 'dl_src': '00:00:00:00:00:00',
                            'dl_dst': '00:00:00:00:00:00', 'dl_vlan': 0,
                            'dl_type': 0, 'nw_src': '0.0.0.0',
                            'nw_dst': '0.0.0.0', 'tp_src': 0, 'tp_dst': 0},
                        'priority': 50000, 'actions': [
                            {'action_type':'output','port': 65533}
                        ]}
                    flow_dict['match'][settings.COLOR_FIELD] = \
                        self.color_to_field(
                            self.switches[neighbor]['color'],
                            settings.COLOR_FIELD
                        )
                    flow = Flow10.from_dict(
                        flow_dict,
                        self.controller.get_switch_by_dpid(neighbor)
                    )
                    switch_dict['flows'][neighbor] = flow
                    returned = requests.post(url % dpid, json=[flow.as_dict()])
                    if returned.status_code // 100 != 2:
                        log.error('Flow manager returned an error inserting '
                                  'flow. Status code %s, flow id %s.' %
                                  (returned.status_code, flow.id))

    def shutdown(self):
        """This method is executed when your napp is unloaded.

        If you have some cleanup procedure, insert it here.
        """
        pass

    @staticmethod
    @listen_to('kytos/of_core.messages.in.ofpt_port_status')
    def update_link_on_port_status_change(event):
        port_status = event.message
        reasons = ['CREATED', 'DELETED', 'MODIFIED']
        switch = event.source.switch
        port_no = port_status.desc.port_no
        reason = reasons[port_status.reason.value]

        if reason is 'MODIFIED':
            interface = switch.get_interface_by_port_no(port_no.value)
            for endpoint, _ in interface.endpoints:
                if isinstance(endpoint, Interface):
                    interface.delete_endpoint(endpoint)

    @staticmethod
    def color_to_field(color, field='dl_src'):
        """
        Gets the color number and returns it in a format suitable for the field
        :param color: The color of the switch (integer)
        :param field: The field that will be used to create the flow for the 
        color
        :return: A representation of the color suitable for the given field
        """
        # TODO: calculate field value for other fields
        if field == 'dl_src' or field == 'dl_dst':
            c = color & 0xffffffffffffffff
            int_mac = struct.pack('!Q', c)[2:]
            color_value = ':'.join(['%02x' % b for b in int_mac])
            return color_value.replace('00', 'ee')
        if field == 'nw_src' or field == 'nw_dst':
            c = color & 0xffffffff
            int_ip = struct.pack('!L', c)
            return '.'.join(map(str, int_ip))
        if field == 'in_port' or field == 'dl_vlan' \
                or field == 'tp_src' or field == 'tp_dst':
            c = color & 0xffff
            return c
        if field == 'nw_tos' or field == 'nw_proto':
            c = color & 0xff
            return c

    @rest('colors')
    def rest_colors(self):
        colors = {}
        for dpid, switch_dict in self.switches.items():
            colors[dpid] = {'color_field': settings.COLOR_FIELD,
                            'color_value': self.color_to_field(
                                switch_dict['color'],
                                settings.COLOR_FIELD
                            )}
        return jsonify({'colors': colors})