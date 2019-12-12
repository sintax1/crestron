import logging
import time
import paho.mqtt.client as mqtt
import json
import datetime
from crestron import CrestronClient
import threading


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


class CrestronMQTT:

    def __init__(self, server, port, username, password):
        self.client = mqtt.Client(client_id="crestron")
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.connected = False
        self.crestron_heartbeat_timeout = 10 # seconds

    def connect(self):
        logging.debug("connect")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.username_pw_set(self.username, self.password)
        self.client.connect(self.server, self.port, 60)

    def run(self):
        self.connect()
        while True:
            logging.debug("loop")
            self.client.loop()

    def crestron_connect(self):
        t1 = threading.Thread(target=self.__crestron_connect, args=())
        t1.daemon = True
        t1.start()
    
    def __crestron_connect(self):
        # Connect to Crestron
        logging.debug("crestron_connect")
        self.crestron_client = CrestronClient('192.168.7.78', 41790, 1234)
        self.crestron_client.on_crestron_data_received = self.on_crestron_data_received
        self.crestron_client.run(heartbeat_timeout=self.crestron_heartbeat_timeout)

    # The callback for when the client receives a CONNACK response from the server.p
    def on_connect(self, client, userdata, flags, rc):
        logging.debug("Connected with result code "+str(rc))
        logging.debug(mqtt.connack_string(rc))

        self.client.message_callback_add('crestron/button', self.cb_button)
        """
        self.mqtt.client.message_callback_add('crestron/power', self.cb_power)
        self.mqtt.client.message_callback_add('crestron/volume/up', self.cb_volume_up)
        self.mqtt.client.message_callback_add('crestron/volume/down', self.cb_volume_down)
        self.mqtt.client.message_callback_add('crestron/volume/mute', self.cb_volume_mute)
        self.mqtt.client.message_callback_add('crestron/source/select', self.cb_source_select)
        self.mqtt.client.message_callback_add('crestron/volume/set', self.cb_volume_set)
        """

        self.client.subscribe("crestron/#")
        self.connected = True
 
        self.crestron_connect()

    def _callback(func):
        def wrapper(self, *args, **kwargs):
            logging.debug("Is connected: {}".format(self.crestron_client.is_connected))
            if not self.crestron_client.is_connected:
                self.crestron_connect()
                while not self.crestron_client.is_connected:
                    time.sleep(1)
            func(self, *args, **kwargs)
        return wrapper

    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client, userdata, msg):
        logging.debug(msg.topic+" "+str(msg.payload))

    @_callback
    def cb_button(self, client, userdata, msg):
        logging.debug("MQTT button: {}".format(msg))
        data = json.loads(msg.payload)
        self.crestron_client.button_press(data['id'])

    def on_crestron_data_received(self, data_type, id, value):
        payload = {
            'data_type': data_type,
            'id': id,
            'value': value
        }
        self.client.publish('crestron/data', json.dumps(payload))

if __name__ == "__main__":
    client = CrestronMQTT(server="192.168.7.254", port=1883, username="mqtt", password="4FX6h2QilFbp58")
    client.run()
