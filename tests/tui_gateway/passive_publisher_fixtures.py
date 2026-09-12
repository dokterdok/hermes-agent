"""Publisher fixtures using real stores and an ordinary in-process receiver."""

import copy

from gateway import hosted_room_links as links
from gateway import hosted_room_peer as peer
from gateway import hosted_room_replica_ingress as ingress
from gateway import hosted_room_replica_retirement as retirement
from gateway import hosted_room_passive_protocol as protocol
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import TARGET

KEY = ("room", "reviewer")


def save_link(pair, *, url="http://127.0.0.1:9876"):
    catalog = peer.GatewayRoomCatalog.from_mapping(peer.catalog_mapping(
        installation_id=TARGET, target_profile="default", protocol_versions=(peer.PROTOCOL_VERSION,),
        link_modes=("direct",), persistent_process=True, attachments=False, endpoint={"available": False, "reason": "not_configured"}))
    link = links.make_stored_link(room_id="room", member_id="reviewer", target_url=url,
        target_profile="default", grant=pair.token, catalog=catalog,
        cancellation_scope_id="copy-scope", trace_id="copy-trace")
    links.save_room_link(pair.source, link)
    return link


def enroll(pair, url="http://127.0.0.1:9876"):
    entry = retirement.prepare_home_enrollment(pair.source, room_id="room", target_install_id=TARGET,
        endpoint=url, local_gateway_id=pair.gateway, secret=pair.secret)
    history = retirement.home_enrollment_history(pair.source, enrollment_id=entry["enrollment_id"])
    retirement.enroll_target(pair.target, enrollment=entry, target_install_id=TARGET, **history)
    return entry


class Receiver:
    def __init__(self, pair):
        self.pair = pair
        self.pages = []
        self.records = []
        self.after_page = None
        self.after_work = None

    def probe(self, *, grant):
        return {"passive_replication": protocol.passive_capabilities(),
            "retirement_enrollment": retirement.current_target_enrollment(self.pair.target,
                room_id="room", authority_gateway_id=self.pair.gateway, authority_epoch=self.pair.epoch)}

    def replicate_page(self, *, grant, target_profile, **body):
        # Sending must not span a source writer transaction.
        with rooms._transaction(self.pair.source, immediate=True):
            pass
        self.pages.append(copy.deepcopy(body["page"]))
        result = ingress.ingest_granted_page(self.pair.target, token=grant, secret=self.pair.secret,
            target_install_id=TARGET, target_profile=target_profile, **body)
        return self.after_page(result) if self.after_page else result

    def replicate_work_records(self, *, grant, target_profile, record):
        with rooms._transaction(self.pair.source, immediate=True):
            pass
        self.records.append(copy.deepcopy(record))
        result = work.ingest(self.pair.target, record=record, token=grant, secret=self.pair.secret,
                            target_install_id=TARGET, target_profile=target_profile)
        return self.after_work(result) if self.after_work else result
