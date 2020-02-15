import asyncio
from sys import stdout
import xml.etree.ElementTree as ET
import re
import logging
import time
import paho.mqtt.client as mqtt
import json
import datetime


logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.DEBUG,
    datefmt='%Y-%m-%d %H:%M:%S')

zone_to_button = {
    '1': 10,
    '2': 11,
    '3': 12
}

source_to_button = {
    'Alexa': 15,
    'Bluetooth': 14
}

control_to_button = {
    'power': 4,
    'vol_up': 6,
    'vol_down': 7,
    'vol_mute': 8
}

def num_map(from_min, from_max, to_min, to_max, value):
    """convert a number within a range to a different range
    """
    from_scale = from_max - from_min
    to_scale = to_max - to_min
    value_scaled = float(value - from_min) / float(from_scale)
    return to_min + (value_scaled * to_scale)


def set_list_value(l, i, v):
    try:
        l[i] = v
    except IndexError:
        for _ in range(i-len(l)+1):
            l.append(None)
        l[i] = v

class MQTT:

    def __init__(self, server="192.168.7.254", port=1883, username="mqtt", password="4FX6h2QilFbp58"):
        self.client = mqtt.Client(client_id="crestron")
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.connected = False

    def connect(self):
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.username_pw_set(self.username, self.password)
        self.client.connect(self.server, self.port, 60)

    async def run(self):
        return self.client.loop_start()

    # The callback for when the client receives a CONNACK response from the server.
    def on_connect(self, client, userdata, flags, rc):
        logging.debug("Connected with result code "+str(rc))
        logging.debug(mqtt.connack_string(rc))
        #self.client.subscribe("crestron/#")
        self.connected = True

    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client, userdata, msg):
        logging.debug(msg.topic+" "+str(msg.payload))

class CrestronClient(asyncio.Protocol):
    def __init__(self, loop, passcode, mqtt, **kwargs):
        self.loop = loop
        self.passcode = passcode
        self.mqtt = mqtt
        self.is_open = False
        self.is_connected = False
        self.states = {
            'serial': [],
            'analog': [],
            'digital': []
        }
        self.zone_settings = {}
        self.pause_heartbeats = False

        self.mqtt.client.message_callback_add('crestron/power', self.cb_power)
        self.mqtt.client.message_callback_add('crestron/volume/up', self.cb_volume_up)
        self.mqtt.client.message_callback_add('crestron/volume/down', self.cb_volume_down)
        self.mqtt.client.message_callback_add('crestron/volume/mute', self.cb_volume_mute)
        self.mqtt.client.message_callback_add('crestron/source/select', self.cb_source_select)
        self.mqtt.client.message_callback_add('crestron/volume/set', self.cb_volume_set)

    def cb_source_select(self, client, userdata, msg):
        logging.debug("Zone input")
        logging.debug("MQTT MSG RECV: {}".format(msg.payload))

        payload = json.loads(msg.payload)

        zone_button = zone_to_button[payload['zone']]
        source_button = source_to_button[payload['source']]

        # Press the zone button
        self.__button_press(zone_button)
        # Press the source button
        self.__button_press(source_button)

    def cb_power(self, client, userdata, msg):
        logging.debug("Power")
        cmd = msg.payload.decode('utf-8')

        if self.__is_power_on() and cmd == 'OFF':
            self.__button_press(control_to_button['power'])
        elif not self.__is_power_on() and cmd == 'ON':
            self.__restore_zone_settings()

        time.sleep(1)
        state = 'ON' if self.__is_power_on() else 'OFF'
        self.mqtt.client.publish('crestron/powerState', state)

    def cb_volume_up(self, client, userdata, msg):
        logging.debug("Volume Up")
        self.__button_press(control_to_button['vol_up'])

    def cb_volume_down(self, client, userdata, msg):
        logging.debug("Volume Down")
        self.__button_press(control_to_button['vol_down'])

    def cb_volume_mute(self, client, userdata, msg):
        logging.debug("Volume Mute")
        self.__button_press(control_to_button['vol_mute'])

    def __get_analog_value(self, id):
        try:
            return int(self.states['analog'][id])
        except IndexError:
            return 0

    def cb_volume_set(self, client, userdata, msg):
        logging.debug("Volume Set")

        data = json.loads(msg.payload)

        # Select the right zone before adjusting the volume
        if data["zone"] == "input_number.crestron_zone1_volume":
            self.__button_press(zone_to_button['1'])
        if data["zone"] == "input_number.crestron_zone2_volume":
            self.__button_press(zone_to_button['2'])
        if data["zone"] == "input_number.crestron_zone3_volume":
            self.__button_press(zone_to_button['3'])

        time.sleep(1)

        tvol = int(data["volume"])

        # convert 0-100 scale to crestron 0-65535 scale
        logging.debug("orig volume: {}".format(tvol))
        tvol = num_map(0, 100, 0, 65535, tvol)
        logging.debug("scaled volume: {}".format(tvol))

        cvol = self.__get_analog_value(1)

        logging.debug("Target Volume: {}".format(tvol))
        stime = datetime.datetime.now()

        # Don't let heartbeats interrupt our commands
        self.pause_heartbeats = True

        if cvol < tvol:
            # Increase to target volume
            self.sendData('digital', control_to_button['vol_up'], 'true', 'true')
            while 65535 > cvol and cvol < tvol:
                # Need to periodically resend the button signal if the target volume is far away
                if (datetime.datetime.now()-stime).total_seconds() >= 1:
                    self.sendData('digital', control_to_button['vol_up'], 'true', 'true')
                    stime = datetime.datetime.now()
                    logging.debug("Current: {}, Target: {}".format(cvol, tvol))
                cvol = self.__get_analog_value(1)
            self.sendData('digital', control_to_button['vol_up'], 'false', 'false')

        else:
            # Decrease to target volume
            self.sendData('digital', control_to_button['vol_down'], 'true', 'true')
            while 0 < cvol and cvol > tvol:
                # Need to periodically resend the button signal if the target volume is far away
                if (datetime.datetime.now()-stime).total_seconds() >= 1:
                    self.sendData('digital', control_to_button['vol_down'], 'true', 'true')
                    stime = datetime.datetime.now()
                    logging.debug("Current: {}, Target: {}".format(cvol, tvol))
                cvol = self.__get_analog_value(1)
            self.sendData('digital', control_to_button['vol_down'], 'false', 'false')

        self.pause_heartbeats = False

    def send(self, data):
        if data:
            logging.debug('SEND: {}'.format(data))
            self.transport.write(data.encode())

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

    def connection_made(self, transport):
        logging.debug("Connection made")

        self.sockname = transport.get_extra_info("sockname")
        self.transport = transport
        self.is_open = True

    def connection_lost(self, exc):
        self.is_open = False
        self.loop.stop()

    def data_received(self, data):
        if data:
            self.process_data(data.decode("utf-8"))

    def process_data(self, data):
        logging.debug("RECV: {}".format(data))
        root = ET.fromstring("<root>{}</root>".format(data))
        self.__process_xml(root)

    async def heartbeat(self):
        while True:
            await asyncio.sleep(10)
            if self.is_connected and not self.pause_heartbeats:
                self.__heartbeatRequest()
                state = 'ON' if self.__is_power_on() else 'OFF'
                self.mqtt.client.publish('crestron/powerState', state)

    def __button_press(self, button_id):
        self.sendData('digital', button_id, 'true')
        self.sendData('digital', button_id, 'false')

    def __is_power_on(self):
        d = self.states['digital']
        try:
            return d[zone_to_button['1']] == 'true' or d[zone_to_button['2']] == 'true' or d[zone_to_button['3']] == 'true'
        except IndexError:
            return False

    def __get_selected_zone_button(self):
        for i in [10, 11, 12]:
            if self.states['digital'][i] == 'true':
                return i

    def __normalize_button_id(self, id):
        m = {
            20: 15,
            21: 14,
            23: 15,
            24: 14,
            27: 15,
            28: 14
        }

        if id in [4, 6, 7, 8, 10, 11, 12]: return id

        return m[id]

    def __store_state(self, data_type, id, value):
        logging.debug("Storing State: {} {}".format(id, value))
        set_list_value(self.states[data_type], id, value)

        # Save zone/source settings
        if self.__is_power_on() and data_type == 'digital' and value == 'true':
            id = self.__normalize_button_id(id)
            
            if id in [14, 15]:
                selected_zone_button = self.__get_selected_zone_button()
                if selected_zone_button:
                    self.zone_settings[selected_zone_button] = id

        logging.debug("Zone to Source States: {}".format(self.zone_settings))

    def __restore_zone_settings(self):
        for zone, source in self.zone_settings.items():
            self.__button_press(zone)
            # For some reason we need to toggle the sources for crestron to make the change
            if source == 14:
                self.__button_press(15)
            else:
                self.__button_press(14)
            self.__button_press(source)


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
                    logging.debug("Heartbeat")
                    #self.__heartbeatRequest()
                elif cresnet.find('.//disconnectRequest') != None:
                    logging.info("Disconnected")
                    self.loop.stop()
                # data coming in
                elif cresnet.find('.//data') != None:
                    payload = {}
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
    loop = asyncio.get_event_loop()
    
    # Connect to MQTT message queue
    mqtt_client = MQTT()
    mqtt_client.connect()
    asyncio.ensure_future(mqtt_client.run())

    # Connect to Crestron
    client = CrestronClient(loop, 1234, mqtt_client)
    coro = loop.create_connection(lambda: client, '192.168.7.78', 41790)
    server = loop.run_until_complete(coro)

    # Start the heartbeat thread to keep the connection alive
    asyncio.ensure_future(client.heartbeat())

    loop.run_forever()
    loop.close()
