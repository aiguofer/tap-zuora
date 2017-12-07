import singer
from xml.etree import ElementTree

from tap_zuora import apis


TYPE_MAP = {
    "picklist": "string",
    "text": "string",
    "boolean": "boolean",
    "integer": "integer",
    "decimal": "number",
    "date": "date",
    "datetime": "datetime",
}

REPLICATION_KEYS = [
    "UpdatedDate",
    "TransactionDate",
    "UpdatedOn",
]

REQUIRED_KEYS = ["Id"] + REPLICATION_KEYS

CAN_BE_NULL_FIELD_PATHS = set([
    "Export.Size",
    "Import.TotalCount",
    "Import.ResultResourceUrl",
    "InvoiceItem.UOM",
    "Payment.GatewayResponse",
    "Payment.GatewayResponseCode",
    "RatePlanCharge.UOM",
])


LOGGER = singer.get_logger()


def parse_field_element(field_element):
    name = field_element.find("name").text
    type = TYPE_MAP.get(field_element.find("type").text, None)
    required = field_element.find("required").text.lower() == "true" or name in REQUIRED_KEYS
    contexts = [t.text for t in field_element.find("contexts").getchildren()]
    return {
        "name": name,
        "type": type,
        "required": required,
        "contexts": contexts,
    }


def get_field_dict(client, stream_name):
    endpoint = "v1/describe/{}".format(stream_name)
    xml_str = client.rest_request("GET", endpoint).content
    etree = ElementTree.fromstring(xml_str)

    field_dict = {}
    for field_element in etree.find("fields").getchildren():
        field_info = parse_field_element(field_element)

        if field_info["type"] is None:
            LOGGER.debug("%s.%s has an unsupported data type", stream_name, field_info["name"])
        elif "export" not in field_info["contexts"]:
            LOGGER.debug("%s.%s not available", stream_name, field_info["name"])
            # show the field in the UI, but it needs to be 'unsupported'

        else:
            field_dict[field_info["name"]] = {
                "type": field_info["type"],
                "required": field_info["required"],
            }

    return field_dict


def get_replication_key(properties):
    for key in REPLICATION_KEYS:
        if key in properties:
            return key


def discover_stream_names(client):
    xml_str = client.rest_request("GET", "v1/describe").content
    etree = ElementTree.fromstring(xml_str)
    return [t.text for t in etree.findall("./object/name")]


def discover_stream(client, stream_name, force_rest):
    field_dict = get_field_dict(client, stream_name)

    properties = {}
    for field_name, props in field_dict.items():
        field_properties = {}

        if props["type"] in ["date", "datetime"]:
            field_properties["type"] = "string"
            field_properties["format"] = "date-time"
        else:
            field_properties["type"] = props["type"]

        path = "{}.{}".format(stream_name, field_name)
        if not props["required"] or path in CAN_BE_NULL_FIELD_PATHS:
            field_properties["type"] = [field_properties["type"], "null"]

        if field_name in REQUIRED_KEYS:
            field_properties["inclusion"] = "automatic"
        else:
            field_properties["inclusion"] = "available"

        properties[field_name] = field_properties

    # Zuora sends back more entities than are actually available. We need to
    # run a sample export to test if the stream is available. If we are using
    # AQuA, we also need to see if we can use the Deleted property for that
    # stream.
    if force_rest:
        status = apis.Rest.stream_status(client, stream_name)
    else:
        status = apis.Aqua.stream_status(client, stream_name)

    # If the entity is unavailable, we need to return None
    if status == "unavailable":
        return None
    elif status == "available_with_deleted":
        properties["Deleted"] = {"type": "boolean"}

    stream = {
        "tap_stream_id": stream_name,
        "stream": stream_name,
        "key_properties": ["Id"],
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        },
    }

    replication_key = get_replication_key(properties)
    if replication_key:
        stream["replication_key"] = replication_key

    return stream


def discover_streams(client, force_rest):
    streams = []
    for stream_name in discover_stream_names(client):
        stream = discover_stream(client, stream_name, force_rest)
        if stream:
            streams.append(stream)

    return streams
