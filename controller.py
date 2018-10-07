import eventlet
# BGPSpeaker needs sockets patched
eventlet.monkey_patch()

import logging
import os
import sys
import array
import re
import json
import collections
from ryu import cfg
from ryu.utils import load_source
from ryu.base import app_manager
from ryu.ofproto import ofproto_v1_3, ether
from ryu.topology import event
from ryu.lib.packet import packet, bgp, ethernet, tcp, ipv4
from ryu.lib.packet.bgp import _ExtendedCommunity
from ryu.lib.stringify import StringifyMixin
from ryu.lib.type_desc import TypeDisp
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls, MAIN_DISPATCHER
from ryu.controller.event import EventBase
from ryu.services.protocols.bgp import application as bgp_application
from ryu.services.protocols.bgp import peer
from ryu.services.protocols.bgp.base import BGPSException
from ryu.services.protocols.bgp.bgpspeaker import BGPSpeaker
from ryu.services.protocols.bgp.info_base.base import AttributeMap, PrefixFilter, ASPathFilter, Path

CONF = cfg.CONF['bgp-app']
class ApplicationException(BGPSException):
    """
    Specific Base exception related to `BSPSpeaker`.
    """
    pass

def load_config(config_file):
    """
    Validates the given file for use as the settings file for BGPSpeaker
    and loads the configuration from the given file as a module instance.
    """
    if not config_file or not os.path.isfile(config_file):
        raise ApplicationException(
            desc='Invalid configuration file: %s' % config_file)
    # Loads the configuration from the given file, if available.
    try:
        return load_source('bgpspeaker.application.settings', config_file)
    except Exception as e:
        raise ApplicationException(desc=str(e))

@_ExtendedCommunity.register_unknown_type()
class BGPNonBestExtendedCommunity(_ExtendedCommunity):
    _VALUE_PACK_STR = '!B3s'  # sub type, id
    _VALUE_FIELDS = ['subtype', 'id']

    def __init__(self, type_, **kwargs):
        super(BGPNonBestExtendedCommunity, self).__init__(type_=type_)
        self.do_init(BGPNonBestExtendedCommunity, self, kwargs, type_=type_)

class Bgp_Non_Best_Choice(app_manager.RyuApp):
    _CONTEXTS = {
        'ryubgpspeaker': bgp_application.RyuBGPSpeaker,
    }

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(Bgp_Non_Best_Choice, self).__init__(*args, **kwargs)

        # Stores "ryu.services.protocols.bgp.application.RyuBGPSpeaker"
        # instance in order to call the APIs of
        # "ryu.services.protocols.bgp.bgpspeaker.BGPSpeaker" via
        # "self.app.speaker".
        # Please note at this time, "BGPSpeaker" is NOT instantiated yet.
        self.RyuBGPSpeaker = kwargs['ryubgpspeaker']
        self.config_file = CONF.config_file
        if self.config_file:
            self.__settings = load_config(self.config_file)
            self.controller_ip = self.__settings.SWITCH_CONTROLLER_INFO.get('controller_ip')
            self.controller_mac = self.__settings.SWITCH_CONTROLLER_INFO.get('controller_mac')
            self.bgp_router_ip = self.__settings.BGP.get('router_id')
            self.bgp_router_mac = self.__settings.SWITCH_CONTROLLER_INFO.get('speaker_mac')
            self.as_number = self.__settings.BGP.get('local_as')
            self.INFO_NEIGHBORS = self.__settings.INFO_NEIGHBORS
            self.neighbors = self.__settings.BGP.get('neighbors')
            self.neighbors_map = self.__settings.BGP
            self.speaker_port = self.__settings.SWITCH_CONTROLLER_INFO.get('speaker_port')
            self.non_best_choices = self.__settings.NON_BEST_CHOICES

        self.nonBestChoice = self.non_best_choices.get('non_best_choice')
        self.nonBestID = self.non_best_choices.get('id')
        self.nonBestRoute = self.non_best_choices.get('route')
        self.subnetNonBestTraffic = self.non_best_choices.get('subnet_non_best_traffic')
        self.ipNonBestTraffic = self.non_best_choices.get('ip_non_best_traffic')
        self.nonBestRouteOrigin = self.non_best_choices.get('non_best_origin')
        self.bgp_nlri = None
        self.nonBestAnnounceListSend = []

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        datapath = ev.switch.dp
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        name2ports = self.name_to_ports(datapath.ports)

        for peer in self.neighbors:
            #regole ARP
            match = parser.OFPMatch(eth_type=ether.ETH_TYPE_ARP, arp_spa=self.bgp_router_ip, arp_tpa=peer['address'])
            actions = [parser.OFPActionSetField(eth_dst=self.INFO_NEIGHBORS.get(peer['address']).get('mac')), 
                        parser.OFPActionOutput(port=name2ports[self.INFO_NEIGHBORS.get(peer['address']).get('port')])]
            self.add_flow(datapath, 20, match, actions, 0)

            #regole per il peering
            match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ip_proto=6, ipv4_dst=peer['address'])
            actions = [parser.OFPActionSetField(eth_dst=self.INFO_NEIGHBORS.get(peer['address']).get('mac')), 
                        parser.OFPActionOutput(port=name2ports[self.INFO_NEIGHBORS.get(peer['address']).get('port')])]
            self.add_flow(datapath, 20, match, actions, 0)

            #regole per annunci non best
            match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ip_proto=6, ipv4_dst=peer['address'])
            actions = [parser.OFPActionSetField(eth_dst=self.INFO_NEIGHBORS.get(peer['address']).get('mac')), 
                        parser.OFPActionOutput(port=name2ports[self.INFO_NEIGHBORS.get(peer['address']).get('port')])]
            self.add_flow(datapath, 20, match, actions, 1)

        match = parser.OFPMatch(eth_type=ether.ETH_TYPE_ARP, arp_tpa=self.bgp_router_ip)
        actions = [parser.OFPActionSetField(eth_dst=self.bgp_router_mac), parser.OFPActionOutput(port=name2ports[self.speaker_port])]
        self.add_flow(datapath, 10, match, actions, 0)

        match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ipv4_dst='1.1.1.1', ipv4_src='2.2.2.2')
        actions = [parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER)]
        self.add_flow(datapath, 100, match, actions, 0)

        match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ip_proto=6, ipv4_dst=self.bgp_router_ip)
        actions = [parser.OFPActionSetField(eth_dst=self.controller_mac), parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER)]
        self.add_flow(datapath, 20, match, actions, 0)

        match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ip_proto=6, in_port=datapath.ofproto.OFPP_CONTROLLER)
        actions = [parser.OFPInstructionGotoTable(1)]
        self.add_flow_go_to_table(datapath, 100, match, actions, 0)

        match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ip_proto=6, in_port=datapath.ofproto.OFPP_CONTROLLER)
        actions = [parser.OFPActionSetField(eth_dst=self.bgp_router_mac), parser.OFPActionOutput(port=name2ports[self.speaker_port])]
        self.add_flow(datapath, 20, match, actions, 1)
        
    def name_to_ports(self, ports):
        name2ports = {}
        for port in ports:
            name2ports[ports[port].name] = ports[port].port_no
        return name2ports

    def add_flow(self, datapath, priority, match, actions, tableID):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority, match=match, instructions=inst, table_id=tableID)
        datapath.send_msg(mod)
        print('regola scritta tabella 0')

    def add_flow_go_to_table(self, datapath, priority, match, actions, tableID):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        mod = parser.OFPFlowMod(datapath=datapath, table_id=tableID, priority=priority, match=match, instructions=actions)
        datapath.send_msg(mod)
        print('regola scritta tabella 1')

    @set_ev_cls(bgp_application.EventBestPathChanged)
    def _best_path_changed_handler(self, ev):
        print('BEST PATH CHANGE: ')
        print(self.RyuBGPSpeaker.speaker.rib_get('ipv4', 'cli'))
        if self.bgp_nlri is not None:
            pass
        elif ev.path.nlri.addr == self.ipNonBestTraffic:
            if type(ev.path.nlri) == bgp.BGPNLRI:
                self.bgp_nlri = ev.path.nlri

    @set_ev_cls(bgp_application.EventAdjRibInChanged)
    def _adj_rib_in_change_handler(self, ev):
        print('ADJ_RIB_IN_CHANGE')
        print(self.RyuBGPSpeaker.speaker.rib_get('ipv4', 'cli'))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        pkt = packet.Packet(msg.data)
        name2ports = self.name_to_ports(datapath.ports)
        nexthopNonBest = None

        # levare il commento per stampare i pacchetti BGPUpdate e la rib 
        """for p in pkt.protocols:
            protocol_name = str(p.protocol_name)
            if re.search('[BGPU]', protocol_name):
                print('BGP UPDATE: ')
                print(self.RyuBGPSpeaker.speaker.rib_get('ipv4', 'cli'))
                print('PACCHETTO BGP', p)"""

        for p in pkt.protocols:
            protocol_name = str(p.protocol_name)

            if p.protocol_name == 'BGPUpdate':
                for attr in p.path_attributes:   
                    if attr.type == 16:
                        print('***********ANNUNCIO NON BEST***********\n')
                        print(p)
                        for pathAttr in p.path_attributes:
                            if pathAttr.type == 3:
                                nexthopNonBest = pathAttr.value
                        best = self.get_nexthop_best()
                        self._handle_bgp_non_best_announce(idNonBestMessage=attr.communities[0].id, 
                                            datapath=datapath, nexthopMessage=nexthopNonBest, first=False, best=best)
                        return

            if protocol_name == 'ipv4':
                if self.nonBestChoice and p.dst == '1.1.1.1':
                    print('ARRIVATO PACCHETTO DUMMY')
                    self.create_non_best_rules(datapath, ofproto, parser)
                    best = self.get_nexthop_best()
                    self._handle_bgp_non_best_announce(idNonBestMessage=self.nonBestID, 
                                            nexthopMessage=best['nexthop'], datapath=datapath, first=True, best=best)
                    return
            
        self._send_packet_to(datapath, pkt)

    def get_nexthop_best(self):
        rib = self.json_loads_byteified(self.RyuBGPSpeaker.speaker.rib_get('ipv4', 'json'))[0]['paths']
        for entry in rib:
            if entry['best']:
                return entry

    def _handle_bgp_non_best_announce(self, idNonBestMessage,  nexthopMessage, datapath, first, best):
        if not first:
            if self.nonBestChoice:
                if idNonBestMessage == self.nonBestID:
                    self.delete_non_best_rules(datapath)
                    print('*************LA ROTTA NON BEST CONTIENE UN CICLO**************')
                    return

        sendCommunity = False

        if nexthopMessage == best['nexthop'] or nexthopMessage == self.nonBestRoute and not(idNonBestMessage in self.nonBestAnnounceListSend):
            sendCommunity = True

        if sendCommunity:
            for elem in self.INFO_NEIGHBORS:
                if elem != nexthopMessage and elem != self.nonBestRouteOrigin and not(first and elem == self.nonBestRoute):
                    packet_non_best = self.create_non_best_packet(mac_dst=self.INFO_NEIGHBORS.get(elem).get('mac'),
                                                mac_src=self.bgp_router_mac, ip_dst=elem, 
                                                ip_src=self.bgp_router_ip, idNonBest=idNonBestMessage, best=best)
                    self._send_packet_to(datapath, packet_non_best)
                    print('*************PROPAGATO ANNUNCIO NON BEST TO', elem, '****************')
                    print('\n')
            self.nonBestAnnounceListSend.append(idNonBestMessage)

        else:
            print('**********ANNUNCIO NON BEST SCARTATO***************\n')

    def create_non_best_packet(self, mac_dst, mac_src, ip_dst, ip_src, idNonBest, best):
        _eth = ethernet.ethernet(dst=mac_dst, src=mac_src, ethertype=ether.ETH_TYPE_IP)
        _ip = ipv4.ipv4(identification=1111, flags=2, ttl=64, proto=6, src=ip_src, dst=ip_dst)
        _tcp = tcp.tcp(dst_port=179)
        _nlri = []
        _nlri.append(self.bgp_nlri)
        _pattrs = self.create_path_attr_bgp(idNonBest=idNonBest, best=best)
        _bgp = bgp.BGPUpdate(path_attributes=_pattrs, nlri=_nlri)
        p = packet.Packet()
        p.add_protocol(_eth)
        p.add_protocol(_ip)
        p.add_protocol(_tcp)
        p.add_protocol(_bgp)
        p.serialize()
        return p

    def create_path_attr_bgp(self, idNonBest, best):
        pattrs = []
        communities = []
        origin = bgp.BGPPathAttributeOrigin(flags=64, type_=1, value=0)
        next_hop = bgp.BGPPathAttributeNextHop(flags=64, type_=3, value=self.bgp_router_ip)
        value = best['aspath']
        value = [self.as_number] + value
        aspath = bgp.BGPPathAttributeAsPath(flags=64, type_=2, value=[value])
        ext_comm_non_best = BGPNonBestExtendedCommunity(type_=16, subtype=77, id=idNonBest) 
        communities.append(ext_comm_non_best)
        extended_community = bgp.BGPPathAttributeExtendedCommunities(communities=communities)
        pattrs.append(origin)
        pattrs.append(aspath)
        pattrs.append(next_hop)
        pattrs.append(extended_community)
        return pattrs
      
    def _send_packet_to(self, datapath, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        pkt.serialize()
        data = pkt.data
        actions = [parser.OFPActionOutput(ofproto.OFPP_TABLE)]
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=ofproto.OFP_NO_BUFFER, 
                                in_port=datapath.ofproto.OFPP_CONTROLLER, actions=actions, data=data)
        datapath.send_msg(out)

    def create_non_best_rules(self, datapath, ofproto, parser):
        rib = self.json_loads_byteified(self.RyuBGPSpeaker.speaker.rib_get('ipv4', 'json'))[0]['paths']
        name2ports = self.name_to_ports(datapath.ports)
        for entry in rib:
            if not entry['best']:
                if self.nonBestRoute == entry['nexthop']:
                    ip_dst = self.subnetNonBestTraffic
                    eth_dst = self.INFO_NEIGHBORS.get(entry['nexthop']).get('mac')
                    port_dst = name2ports[self.INFO_NEIGHBORS.get(entry['nexthop']).get('port')]
                    
                    match = parser.OFPMatch(eth_type=ether.ETH_TYPE_IP, ipv4_dst=ip_dst)
                    actions = [parser.OFPActionSetField(eth_dst=eth_dst), parser.OFPActionOutput(port=port_dst)]
                    
                    inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                    mod = parser.OFPFlowMod(datapath=datapath, priority=100, match=match, instructions=inst, 
                                table_id=0, cookie=1, cookie_mask=0xFFFFFFFFFFFFFFFF)
                    datapath.send_msg(mod)
                    print('regola rotta non best scritta')        

    def delete_non_best_rules(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        mod = parser.OFPFlowMod(datapath=datapath,
                        cookie=1,
                        cookie_mask=0xFFFFFFFFFFFFFFFF,
                        table_id=ofproto.OFPTT_ALL,
                        command=ofproto.OFPFC_DELETE,
                        out_port=ofproto.OFPP_ANY,
                        out_group=ofproto.OFPG_ANY
                        )
        datapath.send_msg(mod)

    # funzione di supporto per leggere il json che contiene la rib
    def json_loads_byteified(self, json_text):
        return self._byteify(json.loads(json_text, object_hook=self._byteify), ignore_dicts=True)

    # funzione di supporto per leggere il json che contiene la rib
    def _byteify(self, data, ignore_dicts = False):
        # if this is a unicode string, return its string representation
        if isinstance(data, unicode):
            return data.encode('utf-8')
        # if this is a list of values, return list of byteified values
        if isinstance(data, list):
            return [ self._byteify(item, ignore_dicts=True) for item in data ]
        # if this is a dictionary, return dictionary of byteified keys and values
        # but only if we haven't already byteified it
        if isinstance(data, dict) and not ignore_dicts:
            return {
                self._byteify(key, ignore_dicts=True): self._byteify(value, ignore_dicts=True)
                for key, value in data.iteritems()
            }
        # if it's anything else, return it in its original form
        return data