#! /usr/bin/env python
import sys
from scapy.all import *
p = Ether(dst='01:01:01:01:01:01', src='02:02:02:02:02:02')/IP(src='2.2.2.2', dst='1.1.1.1')
if p:
 p.show()
 sendp(p, iface='eth3')