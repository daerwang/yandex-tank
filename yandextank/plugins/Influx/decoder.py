import time


def uts(dt):
    return int(time.mktime(dt.timetuple()))


class Decoder(object):
    def __init__(self, tank_tag, uuid):
        self.tank_tag = tank_tag
        self.uuid = uuid

    def decode_monitoring_item(self, item):
        host, metrics, _, ts = item
        return {
            "measurement": "monitoring",
            "tags": {
                "tank": self.tank_tag,
                "host": host,
                "uuid": self.uuid,
            },
            "time": ts,
            "fields": metrics,
        }

    def decode_monitoring(self, data):
        print data
        return []

    def decode_aggregate(self, data, stat):
        timestamp = int(data["ts"])
        points = [
            {
                "measurement": "overall_quantiles",
                "tags": {
                    "tank": self.tank_tag,
                    "uuid": self.uuid,
                },
                "time": timestamp,
                "fields": {  # quantiles
                    'q' + str(q): value / 1000.0
                    for q, value in zip(data["overall"]["interval_real"]["q"]["q"],
                                        data["overall"]["interval_real"]["q"]["value"])
                },
            }, {
                "measurement": "overall_meta",
                "tags": {
                    "tank": self.tank_tag,
                    "uuid": self.uuid,
                },
                "time": timestamp,
                "fields": {
                    "active_threads": stat["metrics"]["instances"],
                    "RPS": data["overall"]["interval_real"]["len"],
                    "planned_requests": stat["metrics"]["reqps"],
                },
            }, {
                "measurement": "net_codes",
                "tags": {
                    "tank": self.tank_tag,
                    "uuid": self.uuid,
                },
                "time": timestamp,
                "fields": {
                    str(code): int(cnt)
                    for code, cnt in data["overall"]["net_code"]["count"].items()
                },
            }, {
                "measurement": "proto_codes",
                "tags": {
                    "tank": self.tank_tag,
                    "uuid": self.uuid,
                },
                "time": timestamp,
                "fields": {
                    str(code): int(cnt)
                    for code, cnt in data["overall"]["proto_code"]["count"].items()
                },
            },
        ]
        return points
