import json
import time

import paho.mqtt.client as mqtt

from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.database import Database


TAG_READ_TOPIC = "medication/tag/read/+"

def parse_tag_payload(payload: str) -> dict:
    fields = {}
    if not payload:
        return fields

    parts = payload.split(";")
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def build_record_from_tag_message(msg: dict) -> dict | None:
    tag_uid = msg.get("tag_uid")
    payload_raw = msg.get("payload_raw", "")

    fields = parse_tag_payload(payload_raw)
    if not fields:
        return None

    station_value = fields.get("S", "").strip()
    station_id = f"station_{station_value}" if station_value.isdigit() else station_value

    return {
        "medicine_id": fields.get("ID"),
        "patient_id": fields.get("P"),
        "medicine_name": fields.get("N"),
        "dosage_amount": int(fields.get("D", "0")) if fields.get("D") else None,
        "dosage_unit": "TABLET",
        "time_slots": fields.get("T"),
        "meal_rule": fields.get("M"),
        "station_id": station_id,
        "tag_uid": tag_uid,
        "tag_payload": payload_raw,
        "source_method": "tag",
        "active": True
    }


def main():
    print("Starting live tag registry listener...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    db = Database(config["database"], logger)
    db.connect()

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
            print("\nMQTT message received:")
            print(payload_text)

            data = json.loads(payload_text)

            record = build_record_from_tag_message(data)
            if record is None:
                print("No structured tag payload found. UID was read, but no medicine record stored.")
                return

            ok = db.upsert_registered_medicine(record)
            print("Database upsert:", ok)

            saved = db.get_registered_medicine_by_tag_uid(record["tag_uid"])
            print("Saved record:")
            print(saved)

        except Exception as e:
            print(f"Error handling tag message: {e}")

    client = mqtt.Client(client_id="pi_tag_registry_listener", protocol=mqtt.MQTTv311)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(broker_host, broker_port, 60)
    client.loop_start()

    print("Tap your written sticker tag on the M5Stick RC522 reader.")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping listener...")
    finally:
        client.loop_stop()
        client.disconnect()
        db.cleanup()
        print("Listener stopped.")


if __name__ == "__main__":
    main()
