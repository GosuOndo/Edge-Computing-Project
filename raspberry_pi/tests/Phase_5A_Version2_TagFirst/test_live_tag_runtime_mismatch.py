import json
import time

import paho.mqtt.client as mqtt

from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.database import Database
from raspberry_pi.modules.tag_manager import TagManager


TAG_READ_TOPIC = "medication/tag/read/+"

# Intentionally wrong expectation for negative test
EXPECTED_MEDICINE_ID = "M999"
EXPECTED_STATION_ID = "station_1"


def main():
    print("Starting live tag runtime MISMATCH test...")
    print(f"Expected medicine_id: {EXPECTED_MEDICINE_ID}")
    print(f"Expected station_id: {EXPECTED_STATION_ID}")
    print("Tap the same sticker tag again. This test should FAIL the medicine match.\n")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    db = Database(config["database"], logger)
    db.connect()

    tag_manager = TagManager(logger)

    broker_host = config["mqtt"]["broker_host"]
    broker_port = config["mqtt"]["broker_port"]

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT broker.")
            client.subscribe(TAG_READ_TOPIC, qos=1)
            print(f"Subscribed to {TAG_READ_TOPIC}")
        else:
            print(f"MQTT connect failed with rc={rc}")

    def on_message(client, userdata, msg):
        try:
            payload_text = msg.payload.decode("utf-8")
            print("\nMQTT tag message received:")
            print(payload_text)

            scan_msg = json.loads(payload_text)

            tag_uid = scan_msg.get("tag_uid")
            db_record = db.get_registered_medicine_by_tag_uid(tag_uid)

            if db_record is None:
                db_record = tag_manager.build_record_from_scan(scan_msg)

            print("\nResolved tag record:")
            print(db_record)

            result = tag_manager.verify_scan_against_expected(
                db_record,
                expected_medicine_id=EXPECTED_MEDICINE_ID,
                expected_station_id=EXPECTED_STATION_ID
            )

            print("\nRuntime mismatch result:")
            print(result)

        except Exception as e:
            print(f"Error handling runtime tag mismatch: {e}")

    client = mqtt.Client(client_id="pi_tag_runtime_mismatch", protocol=mqtt.MQTTv311)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(broker_host, broker_port, 60)
    client.loop_start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping runtime mismatch test...")
    finally:
        client.loop_stop()
        client.disconnect()
        db.cleanup()
        print("Runtime mismatch test stopped.")


if __name__ == "__main__":
    main()
