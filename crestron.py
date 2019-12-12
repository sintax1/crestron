import asyncio
from sys import stdout
import xml.etree.ElementTree as ET
import re
import logging
import time
import json
import datetime
from utils import num_map, set_list_value
from conf import zone_to_button, source_to_button, control_to_button


logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.DEBUG,
    datefmt='%Y-%m-%d %H:%M:%S')


class CrestronClient(asyncio.Protocol):
    def __init__(self, crestron_ip, crestron_port, passcode, **kwargs):
        self.crestron_ip = crestron_ip
        self.crestron_port = crestron_port
        self.passcode = passcode
        self.is_open = False
        self.is_connected = False
        self.heartbeat_task = None
        self.pause_heartbeats = False
        self.heartbeat_timeout = 10 # seconds
        self.last_activity = time.time()
        self.states = {
            'serial': [],
            'analog': [],
            'digital': []
        }

    ## asyncio methods

    def connection_made(self, transport):
        logging.debug("Connection made")

        self.sockname = transport.get_extra_info("sockname")
        self.transport = transport
        self.is_open = True

    def connection_lost(self, exc):
        logging.debug("Connection lost")
        self.is_open = False
        self.is_connected = False
        self.loop.stop()

    def data_received(self, data):
        if data:
            self.__process_data(data.decode("utf-8"))

    def send(self, data):
        if data:
            logging.debug('SEND: {}'.format(data))
            self.transport.write(data.encode())

    ## end asyncio methods

    ## public methods

    def run(self, heartbeat_timeout=None):
        self.heartbeat_timeout = heartbeat_timeout
        self.loop = asyncio.new_event_loop()
        coro = self.loop.create_connection(lambda: self, self.crestron_ip, self.crestron_port)
        server = self.loop.run_until_complete(coro)

        # Start the heartbeat thread to keep the connection alive
        self.start_heartbeats(heartbeat_timeout)

        self.loop.run_forever()
        self.loop.close()

    def stop_heartbeats(self):
        self.heartbeat_task.cancel()

    def start_heartbeats(self, timeout=None):
        self.heartbeat_task = asyncio.ensure_future(self.__heartbeat(), loop=self.loop)
        self.heartbeat_timeout_task = asyncio.ensure_future(self.__heartbeat_timeout(timeout), loop=self.loop)

    def crestron_disconnected(self, xml):
        pass

    def crestron_heartbeat_response(self, xml):
        pass

    def on_crestron_data_received(self, data_type, id, value):
        pass

    def sendData(self, data_type, id, value, repeat="true"):
        msg = ''

        if data_type == 'digital':
            msg = '<cresnet><data eom="false" handle="3" slot="0" som="false"><bool id="{}" value="{}" repeating="{}"/></data></cresnet>'.format(id, value, repeat)

        elif data_type == 'analog':
            msg = '<cresnet><data eom="false" handle="3" slot="0" som="false"><i32 id="{}" value="{}" repeating="{}"/></data></cresnet>'.format(id, value, repeat)

        elif data_type == 'serial':
            msg = '<cresnet><data eom="false" handle="3" slot="0" som="false"><string id="{}" value="{}" repeating="{}"/></data></cresnet>'.format(id, value, repeat)
        else:
            raise Exception("Invalid data type: {}".format(data_type))

        self.send(msg)

    def button_press(self, button_id):
        self.sendData('digital', button_id, 'true')
        self.sendData('digital', button_id, 'false')

    ## end Public methods

    ## Private methods

    async def __heartbeat(self):
        while True:
            await asyncio.sleep(10)
            if self.is_connected and not self.pause_heartbeats:
                self.__heartbeatRequest()

    async def __heartbeat_timeout(self, timeout):
        """When timeout expires stop the heartbeats and let the connection die.
        This reduces uneccesary network activity when not in use.
        """
        logging.debug("heartbeat timeout: {}".format(timeout))
        if not timeout:
            return

        while True:
            await asyncio.sleep(1)
            if time.time() - self.last_activity > timeout:
                logging.debug("Timeout")
                self.stop_heartbeats()
                break

    def __process_data(self, data):
        logging.debug("RECV: {}".format(data))
        root = ET.fromstring("<root>{}</root>".format(data))
        self.__process_xml(root)

    def __crestron_disconnected(self, xml):
        self.loop.stop()
        self.crestron_disconnected(xml)

    def __store_state(self, data_type, id, value):
        logging.debug("Storing State: {} {} {}".format(data_type, id, value))

        if data_type == 'digital':
            value = True if value == "true" else False 
        elif data_type == 'analog':
            value = int(value)
        elif data_type == 'serial':
            value = str(value)
        
        set_list_value(self.states[data_type], id, value)

        self.on_crestron_data_received(data_type, id, value)

    def __get_state(self, data_type, id):
        try:
            return self.states[data_type][id]
        except IndexError:
            logging.error('Tried to get an invalid data type or id: {} {}'.format(data_type, id))
            return None

    def __connectRequest(self, passcode):
        # connectRequest
        msg = '<cresnet><control><comm><connectRequest><passcode>{}</passcode><mode isAuthenticationRequired="false" isDigitalRepeatSupported="true" isHeartbeatSupported="true" isProgramReadySupported="true" isUnicodeSupported="true"></mode><device><product>Crestron Mobile Android</product><version> 1.00.01.42</version><maxExtendedLengthPacketMask>3</maxExtendedLengthPacketMask></device></connectRequest></comm></control></cresnet>'.format(passcode)
        self.send(msg)

    def __updateRequest(self):
        # updateRequest
        msg = '<cresnet><data eom="false" som="false"><updateCommand><updateRequest></updateRequest></updateCommand></data></cresnet>'
        self.send(msg)

    def __heartbeatRequest(self):
        # heartbeatRequest
        msg = '<cresnet><control><comm><heartbeatRequest></heartbeatRequest></comm></control></cresnet>'
        self.send(msg)

    def __process_xml(self, xml):
        if xml.find('.//cresnet') != None:
            for cresnet in xml.findall('.//cresnet'):
                # connectRequest
                if cresnet.find('.//status') != None and cresnet.find('.//status').text == '02':
                    logging.debug('Ready to connect')
                    time.sleep(1)
                    self.__connectRequest(self.passcode)
                # updateRequest
                elif cresnet.find('.//code') != None and cresnet.find('.//code').text == '0':
                    logging.debug("Successfully Connected!")
                    self.is_connected = True
                    self.__updateRequest()
                # heartbeat
                elif cresnet.find('.//heartbeatResponse') != None:
                    logging.debug("Heartbeat Response")
                    self.crestron_heartbeat_response(cresnet.find('.//heartbeatResponse'))
                elif cresnet.find('.//disconnectRequest') != None:
                    logging.info("Disconnected")
                    self.__crestron_disconnected(cresnet.find('.//disconnectRequest'))
                # data coming in
                elif cresnet.find('.//data') != None:
                    
                    data = cresnet.find('.//data')

                    if data.find('.//bool') != None:
                        digital = data.find('.//bool')

                        self.__store_state('digital', int(digital.get('id')), digital.get('value'))
                        
                    elif data.find('.//i32') != None:
                        analog = data.find('.//i32')

                        self.__store_state('analog', int(analog.get('id')), analog.text)

                    elif data.find('.//string') != None:
                        serial = data.find('.//string')

                        self.__store_state('serial', int(serial.get('id')), serial.text)

                    else:
                        logging.debug("Nothing important found in data: {}".format(ET.tostring(data)))
    
if __name__ == "__main__":
    # Connect to Crestron
    client = CrestronClient('192.168.7.78', 41790, 1234)
    client.run(heartbeat_timeout=10)
