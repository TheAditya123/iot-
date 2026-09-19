import json
import logging
import ssl
import threading
from paho.mqtt import client as mqtt

LOG = logging.getLogger(__name__)


class MQTTPublisher:
    def __init__(self, config):
        self.config = config
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=config.client_id, protocol=mqtt.MQTTv311)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        context = ssl.create_default_context(cafile=str(config.ca_cert))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(config.client_cert), str(config.private_key))
        self.client.tls_set_context(context)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.max_queued_messages_set(100)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected.set()
            LOG.info("Connected to AWS IoT")
        else:
            self.connected.clear()
            LOG.warning("MQTT connection refused: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()

    def start(self):
        self.client.connect_async(self.config.endpoint, self.config.port, keepalive=60)
        self.client.loop_start()

    def publish(self, event):
        if not self.connected.is_set():
            return False
        try:
            info = self.client.publish(self.config.topic, json.dumps(event, allow_nan=False), qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return False
            info.wait_for_publish(timeout=self.config.timeout)
            return info.is_published()
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.warning("Publish deferred: %s", exc)
            return False

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


def flush_outbox(store, publisher, device_id):
    if publisher is None:
        return
    for doc_id, event in store.pending(device_id):
        if not publisher.publish(event):
            break
        store.mark_published(doc_id)
        LOG.info("Published event %s to AWS topic %s", event.get("event_id"), publisher.config.topic)
