#!/usr/bin/env python3
"""Build and verify Splunk Dashboard Studio views and app objects for UniFi data."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

# --- design tokens ---------------------------------------------------------
PLANE = "#0d0d0d"  # page background
SURFACE = "#1a1a19"  # panel surface
INK = "#ffffff"
INK_2 = "#c3c2b7"
MUTED = "#898781"

# Status palette: reserved, never reused as a series colour.
GOOD = "#0ca30c"  # allowed / low risk
WARNING = "#fab219"  # medium risk
CRITICAL = "#d03b3b"  # blocked / high risk

# Categorical, dark steps, in fixed slot order.
CAT = [
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
]

SEQ_MIN = "#cde2fb"
SEQ_MAX = "#0d366b"

# --- layout grid -----------------------------------------------------------
W = 1440
M = 20  # side margin
GUT = 16  # gutter
CONTENT = W - 2 * M  # 1400

DEFAULT_URL = "https://127.0.0.1:8089"
DEFAULT_APP = "unifi_dashboards"
DEFAULT_AUTH_TOKEN_ENV = "SPLUNK_TOKEN"
DEFAULT_TIMEOUT = 60.0
DEFAULT_OUTPUT_DIR = Path("dist")
DEFAULT_FLOW_INDEX = ""
DEFAULT_FLOW_SOURCETYPE = "unifi:flow"
DEFAULT_EVENT_INDEX = "netops"
DEFAULT_EVENT_SOURCETYPE = "cef"
DEFAULT_EXTERNAL_URL = "https://splunk.example.net"

CONFIG_NAME = "config.local.toml"
EXAMPLE_CONFIG_NAME = "config.example.toml"


def cols(n: int) -> int:
    """Width of one column when the content width is split n ways."""
    return (CONTENT - GUT * (n - 1)) // n


def xs(n: int) -> list[int]:
    """Left edge of each of n equal columns."""
    c = cols(n)
    return [M + i * (c + GUT) for i in range(n)]


class Dash:
    """Builder for Dashboard Studio absolute layout definitions."""

    def __init__(self, title: str, description: str) -> None:
        self.title = title
        self.description = description
        self.viz: dict[str, Any] = {}
        self.ds: dict[str, Any] = {}
        self.structure: list[dict[str, Any]] = []
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"

    def search(self, query: str) -> str:
        did = self._id("ds")
        self.ds[did] = {
            "type": "ds.search",
            "options": {"query": query},
            "name": did,
        }
        return did

    def add(
        self,
        vtype: str,
        options: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
        query: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> str:
        vid = self._id("viz")
        block: dict[str, Any] = {"type": vtype, "options": options}
        if query is not None:
            block["dataSources"] = {"primary": self.search(query)}
        if title:
            block["title"] = title
        if description:
            block["description"] = description
        self.viz[vid] = block
        self.structure.append(
            {
                "item": vid,
                "type": "block",
                "position": {"x": x, "y": y, "w": w, "h": h},
            }
        )
        return vid

    def header(
        self,
        text: str,
        y: int,
        h: int = 56,
        size: int = 22,
        color: str = INK,
        x: int = M,
        w: int = CONTENT,
    ) -> str:
        return self.add(
            "splunk.markdown",
            {
                "markdown": text,
                "fontColor": color,
                "fontSize": size,
                "backgroundColor": "transparent",
            },
            x,
            y,
            w,
            h,
        )

    def kpi(
        self,
        title: str,
        query: str,
        x: int,
        y: int,
        w: int,
        h: int,
        color: str = INK,
        unit: str | None = None,
    ) -> str:
        opts: dict[str, Any] = {
            "majorColor": color,
            "majorFontSize": 40,
            "backgroundColor": SURFACE,
            "sparklineDisplay": "off",
            "trendDisplay": "off",
            "shouldUseThousandSeparators": True,
        }
        if unit:
            opts["unit"] = unit
            opts["unitPosition"] = "after"
        return self.add(
            "splunk.singlevalue", opts, x, y, w, h, query=query, title=title
        )

    def render_definition(self, height: int) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "visualizations": self.viz,
            "dataSources": self.ds,
            "inputs": {
                "input_time": {
                    "type": "input.timerange",
                    "options": {"token": "time", "defaultValue": "-24h@h,now"},
                    "title": "Time range",
                }
            },
            "defaults": {
                "dataSources": {
                    "ds.search": {
                        "options": {
                            "queryParameters": {
                                "earliest": "$time.earliest$",
                                "latest": "$time.latest$",
                            }
                        }
                    }
                }
            },
            "layout": {
                "type": "absolute",
                "options": {
                    "width": W,
                    "height": height,
                    "display": "auto-scale",
                    "backgroundColor": PLANE,
                },
                "structure": self.structure,
                "globalInputs": ["input_time"],
            },
        }

    def render_xml(self, height: int, theme: str = "dark") -> str:
        definition = self.render_definition(height)
        body = json.dumps(definition, indent=2)
        return (
            f'<dashboard version="2" theme="{theme}">\n'
            f"  <label>{xml_escape(self.title)}</label>\n"
            f"  <description>{xml_escape(self.description)}</description>\n"
            f"  <definition><![CDATA[\n{body}\n]]></definition>\n"
            '  <meta type="hiddenElements"><![CDATA[\n'
            '{"hideEdit":false,"hideOpenInSearch":false,"hideExport":false}\n'
            "]]></meta>\n"
            "</dashboard>\n"
        )


def chart_opts(
    colors: list[str],
    legend: str = "off",
    stack: str = "auto",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    o: dict[str, Any] = {
        "seriesColors": colors,
        "backgroundColor": SURFACE,
        "legendDisplay": legend,
        "showYMajorGridLines": True,
        "yAxisAbbreviation": "auto",
    }
    if stack != "auto":
        o["stackMode"] = stack
    if legend != "off":
        o["legendPlacement"] = "right"
    if extra:
        o.update(extra)
    return o


def world_map() -> dict[str, Any]:
    return {"layers": [{"type": "bubble"}], "backgroundColor": SURFACE}


def table_opts(count: int = 20) -> dict[str, Any]:
    return {
        "backgroundColor": SURFACE,
        "count": count,
        "dataOverlayMode": "none",
        "headerVisibility": "fixed",
        "rowNumbers": False,
        "wrap": False,
    }


def flows_dashboard() -> tuple[dict[str, Any], str]:
    d = Dash(
        "UniFi Flow Insights",
        "Every connection the gateway completed: what was allowed, what was blocked, "
        "how risky it was, and where it went.",
    )
    y = 16

    c5, x5 = cols(5), xs(5)
    d.kpi(
        "Total flows",
        "`unifi_flows` | stats count",
        x5[0],
        y,
        c5,
        112,
        color=CAT[0],
    )
    d.kpi(
        "Blocked",
        "`unifi_flows` action=blocked | stats count",
        x5[1],
        y,
        c5,
        112,
        color=CRITICAL,
    )
    d.kpi(
        "High risk",
        "`unifi_flows` risk=high | stats count",
        x5[2],
        y,
        c5,
        112,
        color=WARNING,
    )
    d.kpi(
        "Countries reached",
        "`unifi_flows` dest_region=* | stats dc(dest_region)",
        x5[3],
        y,
        c5,
        112,
        color=CAT[0],
    )
    d.kpi(
        "Data volume",
        (
            "`unifi_flows` | stats sum(bytes) as b "
            "| eval v=round(b/1073741824,1) | fields v"
        ),
        x5[4],
        y,
        c5,
        112,
        color=CAT[0],
        unit=" GB",
    )
    y += 112 + GUT

    d.add(
        "splunk.area",
        chart_opts(
            [GOOD, CRITICAL],
            legend="right",
            stack="stacked",
            extra={"fillOpacity": 0.55, "lineWidth": 2},
        ),
        M,
        y,
        CONTENT,
        250,
        query=(
            "`unifi_flows` "
            '| eval a=if(action="allowed",1,0), b=if(action="blocked",1,0) '
            "| timechart minspan=5m sum(a) as Allowed, sum(b) as Blocked"
        ),
        title="Flow volume over time",
        description="Stacked by disposition; blocked sits on top of allowed.",
    )
    y += 250 + GUT

    d.header("## Where traffic goes", y, h=34, size=16, color=INK_2)
    y += 42

    geo_w = 920
    d.add(
        "splunk.map",
        world_map(),
        M,
        y,
        geo_w,
        380,
        query=(
            "`unifi_flows` dest_zone=External | iplocation dest_ip "
            "| search Country=* "
            "| geostats latfield=lat longfield=lon maxzoomlevel=18 count"
        ),
        title="Where traffic goes",
        description="Outbound flows plotted by destination location.",
    )
    risk_x = M + geo_w + GUT
    d.add(
        "splunk.pie",
        {
            "seriesColors": [CRITICAL, WARNING, GOOD],
            "backgroundColor": SURFACE,
            "showDonutHole": True,
            "legendDisplay": "bottom",
            "labelDisplay": "valuesAndPercentage",
            "collapseThreshold": 0,
            "showOther": False,
        },
        risk_x,
        y,
        CONTENT - geo_w - GUT,
        380,
        query=(
            "`unifi_flows` | stats count by risk "
            "| append [| makeresults count=3 | streamstats count as r "
            '| eval risk=case(r==1,"high",r==2,"medium",r==3,"low"), '
            "count=0 | fields risk count] "
            "| stats sum(count) as count by risk "
            '| eval o=case(risk="high",1,risk="medium",2,risk="low",3) '
            "| sort o | fields risk count"
        ),
        title="Risk mix",
        description="UniFi risk banding: high, medium, low.",
    )
    y += 380 + GUT

    d.header("## Top talkers", y, h=34, size=16, color=INK_2)
    y += 42

    c3, x3 = cols(3), xs(3)
    d.add(
        "splunk.bar",
        chart_opts([CAT[0]]),
        x3[0],
        y,
        c3,
        330,
        query=(
            "`unifi_flows` src=* | stats count by src "
            "| sort -count | head 10 | sort count"
        ),
        title="Busiest clients",
    )
    d.add(
        "splunk.bar",
        chart_opts([CAT[2]]),
        x3[1],
        y,
        c3,
        330,
        query=(
            "`unifi_flows` dest_zone=External "
            "| eval target=if(isnull(dest_domain), dest_ip, mvindex(dest_domain,0)) "
            "| stats count by target | sort -count | head 10 | sort count"
        ),
        title="Top external destinations",
    )
    d.add(
        "splunk.bar",
        chart_opts([CAT[6]]),
        x3[2],
        y,
        c3,
        330,
        query=(
            "`unifi_flows` app=* | stats sum(bytes) as bytes by app "
            "| sort -bytes | head 10 | sort bytes"
        ),
        title="Services by volume",
    )
    y += 330 + GUT

    d.header("## Blocked traffic", y, h=34, size=16, color=INK_2)
    y += 42

    d.add(
        "splunk.table",
        table_opts(50),
        M,
        y,
        CONTENT,
        300,
        query=(
            "`unifi_flows` action=blocked "
            '| eval when=strftime(_time, "%m-%d %H:%M:%S"), '
            'peer=if(direction="incoming", src_ip, dest_ip), '
            'where=coalesce(if(direction="incoming", src_country, dest_country), "-") '
            "| table when direction peer where dest_port transport "
            "rule risk src_zone dest_zone "
            "| sort - when | head 200"
        ),
        title="Blocked flows",
        description=(
            "The peer column is the external address for an inbound block "
            "and target for an outbound block."
        ),
    )
    y += 300 + GUT

    c2, x2 = cols(2), xs(2)
    d.add(
        "splunk.table",
        table_opts(15),
        x2[0],
        y,
        c2,
        320,
        query=(
            "`unifi_flows` | stats count by src_zone dest_zone | sort -count | head 15"
        ),
        title="Zone-to-zone matrix",
    )
    d.add(
        "splunk.table",
        table_opts(15),
        x2[1],
        y,
        c2,
        320,
        query=(
            "`unifi_flows` dest_domain=* "
            "| stats count sum(bytes) as bytes by dest_domain "
            "| sort -count | head 15 | `fmt_bytes(bytes)`"
        ),
        title="Top destination domains",
    )
    y += 320 + M

    return d.render_definition(y), d.render_xml(y)


def threats_dashboard() -> tuple[dict[str, Any], str]:
    d = Dash(
        "UniFi Threat Center",
        "Intrusion detections, firewall blocks, and the security policies "
        "that produced them.",
    )
    y = 16

    c5, x5 = cols(5), xs(5)
    d.kpi(
        "Threats detected",
        "`unifi_threats` | stats count",
        x5[0],
        y,
        c5,
        112,
        color=WARNING,
    )
    d.kpi(
        "Threats blocked",
        "`unifi_threats` action=blocked | stats count",
        x5[1],
        y,
        c5,
        112,
        color=GOOD,
    )
    d.kpi(
        "Passed through",
        "`unifi_threats` action=allowed | stats count",
        x5[2],
        y,
        c5,
        112,
        color=CRITICAL,
    )
    d.kpi(
        "Unique signatures",
        "`unifi_threats` | stats dc(signature)",
        x5[3],
        y,
        c5,
        112,
        color=CAT[0],
    )
    d.kpi(
        "Firewall blocks",
        "`unifi_events` sc4s_class=203 | stats count",
        x5[4],
        y,
        c5,
        112,
        color=CAT[0],
    )
    y += 112 + GUT

    d.add(
        "splunk.column",
        chart_opts([CRITICAL, WARNING, GOOD], legend="right", stack="stacked"),
        M,
        y,
        CONTENT,
        250,
        query=(
            "`unifi_threats` "
            '| eval h=if(severity="high",1,0), m=if(severity="medium",1,0), '
            'l=if(severity="low",1,0) '
            "| timechart minspan=30m sum(h) as High, sum(m) as Medium, "
            "sum(l) as Low"
        ),
        title="Detections over time",
        description="Bands reflect signature threat rating.",
    )
    y += 250 + GUT

    d.header("## What is being detected", y, h=34, size=16, color=INK_2)
    y += 42

    d.add(
        "splunk.table",
        table_opts(20),
        M,
        y,
        860,
        360,
        query=(
            "`unifi_threats` signature=* "
            "| stats count, values(severity) as risk, dc(src) as sources, "
            "latest(_time) as last by signature "
            '| eval last=strftime(last, "%m-%d %H:%M") '
            "| sort -count | head 20"
        ),
        title="Signatures by frequency",
    )
    d.add(
        "splunk.bar",
        chart_opts([CAT[1]]),
        M + 860 + GUT,
        y,
        CONTENT - 860 - GUT,
        360,
        query=(
            "`unifi_threats` rule=* | stats count by rule "
            "| sort -count | head 10 | sort count"
        ),
        title="Threat categories",
    )
    y += 360 + GUT

    d.header("## Where it comes from", y, h=34, size=16, color=INK_2)
    y += 42

    c2, x2 = cols(2), xs(2)
    d.add(
        "splunk.map",
        world_map(),
        x2[0],
        y,
        c2,
        360,
        query=(
            "`unifi_flows` action=blocked direction=incoming "
            "| iplocation src_ip | search Country=* "
            "| geostats latfield=lat longfield=lon maxzoomlevel=18 count"
        ),
        title="Where blocked traffic comes from",
    )
    d.add(
        "splunk.bar",
        chart_opts([CRITICAL]),
        x2[1],
        y,
        c2,
        360,
        query=(
            "`unifi_flows` action=blocked rule=* "
            "| stats count by rule | sort -count | head 10 | sort count"
        ),
        title="Blocking policies",
    )
    y += 360 + GUT

    d.add(
        "splunk.table",
        table_opts(50),
        M,
        y,
        CONTENT,
        320,
        query=(
            "`unifi_threats` "
            '| eval when=strftime(_time, "%m-%d %H:%M:%S") '
            "| rename cef_name as event "
            "| table when event signature rule severity action src dest "
            "dest_port transport src_zone dest_zone "
            "| sort - when | head 200"
        ),
        title="Recent detections",
    )
    y += 320 + M

    return d.render_definition(y), d.render_xml(y)


def clients_dashboard() -> tuple[dict[str, Any], str]:
    d = Dash(
        "UniFi Client & Network Activity",
        "Connected clients, network volume, and controller state changes.",
    )
    y = 16

    c5, x5 = cols(5), xs(5)
    d.kpi(
        "Active clients",
        "`unifi_flows` src_mac=* | stats dc(src_mac)",
        x5[0],
        y,
        c5,
        112,
        color=CAT[0],
    )
    d.kpi(
        "Connects",
        "eventtype=unifi_cef_session_start | stats count",
        x5[1],
        y,
        c5,
        112,
        color=GOOD,
    )
    d.kpi(
        "Disconnects",
        "eventtype=unifi_cef_session_end | stats count",
        x5[2],
        y,
        c5,
        112,
        color=CAT[3],
    )
    d.kpi(
        "Config changes",
        "eventtype=unifi_cef_config_change | stats count",
        x5[3],
        y,
        c5,
        112,
        color=WARNING,
    )
    d.kpi(
        "Admin logins",
        "eventtype=unifi_cef_admin_auth | stats count",
        x5[4],
        y,
        c5,
        112,
        color=CAT[0],
    )
    y += 112 + GUT

    d.add(
        "splunk.line",
        chart_opts(
            [GOOD, CAT[3]],
            legend="right",
            extra={"lineWidth": 2, "markerDisplay": "outlined"},
        ),
        M,
        y,
        CONTENT,
        240,
        query=(
            "`unifi_events` (eventtype=unifi_cef_session_start OR "
            "eventtype=unifi_cef_session_end) "
            "| eval c=if(sc4s_class IN (400,403,520),1,0), "
            "d=if(sc4s_class IN (401,404,521),1,0) "
            "| timechart minspan=15m sum(c) as Connects, sum(d) as Disconnects"
        ),
        title="Client sessions",
    )
    y += 240 + GUT

    d.header("## Traffic by client and network", y, h=34, size=16, color=INK_2)
    y += 42

    c3, x3 = cols(3), xs(3)
    d.add(
        "splunk.bar",
        chart_opts([CAT[0]]),
        x3[0],
        y,
        c3,
        330,
        query=(
            "`unifi_flows` src=* | stats sum(bytes) as bytes by src "
            "| sort -bytes | head 10 | sort bytes"
        ),
        title="Clients by volume",
    )
    d.add(
        "splunk.bar",
        chart_opts([CAT[2]]),
        x3[1],
        y,
        c3,
        330,
        query=(
            "`unifi_flows` src_network=* | stats sum(bytes) as bytes by src_network "
            "| sort -bytes | head 10 | sort bytes"
        ),
        title="Networks by volume",
    )
    d.add(
        "splunk.pie",
        {
            "seriesColors": CAT,
            "backgroundColor": SURFACE,
            "showDonutHole": True,
            "legendDisplay": "bottom",
            "labelDisplay": "valuesAndPercentage",
        },
        x3[2],
        y,
        c3,
        330,
        query=(
            "`unifi_events` wifi_name=* | stats count by wifi_name "
            "| sort -count | head 6"
        ),
        title="WiFi networks",
    )
    y += 330 + GUT

    d.header("## Controller activity", y, h=34, size=16, color=INK_2)
    y += 42

    c2, x2 = cols(2), xs(2)
    d.add(
        "splunk.table",
        table_opts(25),
        x2[0],
        y,
        c2,
        340,
        query=(
            "`unifi_events` (eventtype=unifi_cef_config_change OR "
            "eventtype=unifi_cef_admin_auth) "
            '| eval when=strftime(_time, "%m-%d %H:%M:%S") '
            "| rename cef_name as event, UNIFIaccessMethod as method, "
            "UNIFIsettingsSection as section "
            "| table when event user method section src_ip "
            "| sort - when | head 200"
        ),
        title="Admin and configuration activity",
    )
    d.add(
        "splunk.table",
        table_opts(25),
        x2[1],
        y,
        c2,
        340,
        query=(
            "`unifi_events` (eventtype=unifi_cef_session_start OR "
            "eventtype=unifi_cef_session_end) "
            '| eval when=strftime(_time, "%m-%d %H:%M:%S"), '
            "client=coalesce(client_alias, UNIFIclientHostname, UNIFIclientIp) "
            "| rename cef_name as event, wifi_name as wifi, "
            "UNIFIclientIp as client_ip "
            "| table when event client wifi client_ip | sort - when | head 200"
        ),
        title="Client connect and disconnect",
    )
    y += 340 + GUT

    d.header("## Network health and camera detections", y, h=34, size=16, color=INK_2)
    y += 42

    d.add(
        "splunk.bar",
        chart_opts([CAT[3]]),
        x2[0],
        y,
        c2,
        300,
        query=(
            "`unifi_events` eventtype=unifi_cef_device_alert "
            "| stats count by cef_name | sort -count | head 12 | sort count "
            "| rename cef_name as event"
        ),
        title="Network and device health events",
    )
    d.add(
        "splunk.bar",
        chart_opts([CAT[6]]),
        x2[1],
        y,
        c2,
        300,
        query=(
            "`unifi_events` eventtype=unifi_protect_detection "
            "| stats count by cef_name | sort -count | head 12 | sort count "
            "| rename cef_name as detection"
        ),
        title="Camera detections by type",
    )
    y += 300 + GUT

    d.add(
        "splunk.table",
        table_opts(25),
        M,
        y,
        CONTENT,
        320,
        query=(
            "`unifi_events` (eventtype=unifi_cef_device_alert OR "
            "eventtype=unifi_protect_detection) "
            '| eval when=strftime(_time, "%m-%d %H:%M:%S"), '
            "device=coalesce(dvc_name, UNIFIdeviceName, dvc) "
            "| rename cef_name as event, cef_product as product, "
            "UNIFIcategory as category "
            "| table when product severity event category device "
            "| sort - when | head 200"
        ),
        title="Recent health and detection events",
    )
    y += 320 + M

    return d.render_definition(y), d.render_xml(y)


# --- Settings and configuration --------------------------------------------


@dataclass(frozen=True)
class SplunkSettings:
    url: str = DEFAULT_URL
    app: str = DEFAULT_APP
    auth_token_env: str = DEFAULT_AUTH_TOKEN_ENV
    username: str = ""
    password_env: str = ""
    verify_tls: bool = True
    ca_file: Path | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class DashboardSettings:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    flow_index: str = DEFAULT_FLOW_INDEX
    flow_sourcetype: str = DEFAULT_FLOW_SOURCETYPE
    event_index: str = DEFAULT_EVENT_INDEX
    event_sourcetype: str = DEFAULT_EVENT_SOURCETYPE
    external_url: str = DEFAULT_EXTERNAL_URL
    intrusion_exempt_sources: tuple[str, ...] = ()
    intrusion_exempt_rule: str = ""
    internal_networks: tuple[str, ...] = ()
    admin_networks: tuple[str, ...] = ()
    scan_exempt_sources: tuple[str, ...] = ()
    intrusion_exempt_zones: tuple[str, ...] = ()
    intrusion_exempt_zone_rule: str = ""


@dataclass(frozen=True)
class ToolConfig:
    splunk: SplunkSettings = SplunkSettings()
    dashboards: DashboardSettings = DashboardSettings()


def _reject_unknown(table: dict[str, Any], allowed: Iterable[str], name: str) -> None:
    unknown = set(table) - set(allowed)
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"unknown key in [{name}]: {keys}")


def parse_config(data: dict[str, Any], base_dir: Path = Path(".")) -> ToolConfig:
    _reject_unknown(data, ("splunk", "dashboards"), "root")

    splunk_table = data.get("splunk", {})
    if not isinstance(splunk_table, dict):
        raise ValueError("[splunk] section must be a table")
    _reject_unknown(
        splunk_table,
        (
            "url",
            "app",
            "auth_token_env",
            "username",
            "password_env",
            "verify_tls",
            "ca_file",
            "timeout_seconds",
        ),
        "splunk",
    )

    ca_raw = splunk_table.get("ca_file", "")
    ca_file: Path | None = None
    if isinstance(ca_raw, str) and ca_raw.strip():
        ca_file = Path(ca_raw)
        if not ca_file.is_absolute():
            ca_file = (base_dir / ca_file).resolve()

    splunk_settings = SplunkSettings(
        url=str(splunk_table.get("url", DEFAULT_URL)),
        app=str(splunk_table.get("app", DEFAULT_APP)),
        auth_token_env=str(splunk_table.get("auth_token_env", DEFAULT_AUTH_TOKEN_ENV)),
        username=str(splunk_table.get("username", "")),
        password_env=str(splunk_table.get("password_env", "")),
        verify_tls=bool(splunk_table.get("verify_tls", True)),
        ca_file=ca_file,
        timeout_seconds=float(splunk_table.get("timeout_seconds", DEFAULT_TIMEOUT)),
    )

    dash_table = data.get("dashboards", {})
    if not isinstance(dash_table, dict):
        raise ValueError("[dashboards] section must be a table")
    _reject_unknown(
        dash_table,
        (
            "output_dir",
            "flow_index",
            "flow_sourcetype",
            "event_index",
            "event_sourcetype",
            "external_url",
            "intrusion_exempt_sources",
            "intrusion_exempt_rule",
            "internal_networks",
            "admin_networks",
            "scan_exempt_sources",
            "intrusion_exempt_zones",
            "intrusion_exempt_zone_rule",
        ),
        "dashboards",
    )

    out_raw = dash_table.get("output_dir", DEFAULT_OUTPUT_DIR)
    out_dir = Path(out_raw)
    if not out_dir.is_absolute():
        out_dir = (base_dir / out_dir).resolve()

    def _parse_str_list(raw: Any, name: str) -> tuple[str, ...]:
        if raw is None:
            return ()
        if isinstance(raw, str):
            raise ValueError(f"[{name}] must be a list of strings")
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"[{name}] must be a list of strings")
        for item in raw:
            if not isinstance(item, str):
                raise ValueError(f"all items in [{name}] must be strings")
        return tuple(str(s).strip() for s in raw if str(s).strip())

    intrusion_exempt_sources = _parse_str_list(
        dash_table.get("intrusion_exempt_sources", ()),
        "dashboards.intrusion_exempt_sources",
    )
    intrusion_exempt_rule = str(dash_table.get("intrusion_exempt_rule", "")).strip()

    internal_networks = _parse_str_list(
        dash_table.get("internal_networks", ()), "dashboards.internal_networks"
    )
    admin_networks = _parse_str_list(
        dash_table.get("admin_networks", ()), "dashboards.admin_networks"
    )
    scan_exempt_sources = _parse_str_list(
        dash_table.get("scan_exempt_sources", ()), "dashboards.scan_exempt_sources"
    )
    intrusion_exempt_zones = _parse_str_list(
        dash_table.get("intrusion_exempt_zones", ()),
        "dashboards.intrusion_exempt_zones",
    )
    intrusion_exempt_zone_rule = str(
        dash_table.get("intrusion_exempt_zone_rule", "")
    ).strip()

    dashboard_settings = DashboardSettings(
        output_dir=out_dir,
        flow_index=str(dash_table.get("flow_index", DEFAULT_FLOW_INDEX)),
        flow_sourcetype=str(dash_table.get("flow_sourcetype", DEFAULT_FLOW_SOURCETYPE)),
        event_index=str(dash_table.get("event_index", DEFAULT_EVENT_INDEX)),
        event_sourcetype=str(
            dash_table.get("event_sourcetype", DEFAULT_EVENT_SOURCETYPE)
        ),
        external_url=str(dash_table.get("external_url", DEFAULT_EXTERNAL_URL)),
        intrusion_exempt_sources=intrusion_exempt_sources,
        intrusion_exempt_rule=intrusion_exempt_rule,
        internal_networks=internal_networks,
        admin_networks=admin_networks,
        scan_exempt_sources=scan_exempt_sources,
        intrusion_exempt_zones=intrusion_exempt_zones,
        intrusion_exempt_zone_rule=intrusion_exempt_zone_rule,
    )

    return ToolConfig(splunk=splunk_settings, dashboards=dashboard_settings)


# --- Correlation searches catalog ------------------------------------------


def build_correlation_searches(
    dash_settings: DashboardSettings,
    notes: list[str] | None = None,
) -> list[tuple[str, str, str, str]]:
    searches: list[tuple[str, str, str, str]] = []

    # 1. UniFi - Intrusion detection not blocked
    if dash_settings.intrusion_exempt_sources:
        sources_str = ", ".join(
            f'"{s}"' for s in dash_settings.intrusion_exempt_sources
        )
        src_clause = f"src IN ({sources_str})"
        if dash_settings.intrusion_exempt_rule:
            exemption_clause = (
                f'NOT (rule="{dash_settings.intrusion_exempt_rule}" {src_clause}) '
            )
        else:
            exemption_clause = f"NOT ({src_clause}) "
    else:
        exemption_clause = ""

    s1_query = (
        "`unifi_threats` action=allowed "
        'NOT rule IN ("P2P", "TOR", "Scanning Activity", '
        '"CINS Army Reputation List", "Malicious User Agents", '
        '"Malware", "Exploit Kit") '
        f"{exemption_clause}"
        "| stats count, min(_time) as firstTime, max(_time) as lastTime, "
        "values(signature) as signature, values(dest_ip) as dest_ip, "
        "values(dest_port) as dest_port, values(src_zone) as src_zone, "
        "values(dest_zone) as dest_zone by src, rule, severity "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(
        ("UniFi - Intrusion detection not blocked", s1_query, "-70m", "now")
    )

    # 2. UniFi - Threat reputation or malicious user agent match
    s2_query = (
        "`unifi_threats` rule IN ("
        '"CINS Army Reputation List", "Malicious User Agents", '
        '"Malware", "Exploit Kit") '
        "| stats count, min(_time) as firstTime, max(_time) as lastTime, "
        "values(signature) as signature, values(dest_ip) as dest_ip, "
        "values(action) as action by src, rule "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(
        (
            "UniFi - Threat reputation or malicious user agent match",
            s2_query,
            "-70m",
            "now",
        )
    )

    # 3. UniFi - Outbound scanning activity
    if dash_settings.scan_exempt_sources:
        if len(dash_settings.scan_exempt_sources) == 1:
            scan_src = f'src!="{dash_settings.scan_exempt_sources[0]}" '
        else:
            sources_str = ", ".join(f'"{s}"' for s in dash_settings.scan_exempt_sources)
            scan_src = f"NOT src IN ({sources_str}) "
    else:
        scan_src = ""

    s3_query = (
        f'`unifi_threats` rule="Scanning Activity" {scan_src}'
        "| stats count, dc(dest_ip) as targets, min(_time) as firstTime, "
        "max(_time) as lastTime, values(signature) as signature, "
        "values(dest_port) as dest_port by src, src_zone "
        "| where count >= 5 "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(("UniFi - Outbound scanning activity", s3_query, "-70m", "now"))

    # 4. UniFi - Tor network traffic
    s4_query = (
        '`unifi_threats` rule="TOR" '
        "| stats count, dc(dest_ip) as nodes, min(_time) as firstTime, "
        "max(_time) as lastTime, values(signature) as signature by src, src_zone "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(("UniFi - Tor network traffic", s4_query, "-70m", "now"))

    # 5. UniFi - Blocked inbound flow spike from a single source
    s5_query = (
        "`unifi_blocked` direction=incoming "
        "| stats count, dc(dest_port) as ports, min(_time) as firstTime, "
        "max(_time) as lastTime, values(rule) as rule, "
        "values(src_country) as src_country, "
        "values(transport) as transport by src_ip "
        "| where count >= 25 "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(
        (
            "UniFi - Blocked inbound flow spike from a single source",
            s5_query,
            "-15m",
            "now",
        )
    )

    # 6. UniFi - Controller configuration changed
    s6_query = (
        'eventtype=unifi_cef_config_change user!="Unifi" user!="Network" '
        "| stats count, min(_time) as firstTime, max(_time) as lastTime, "
        "values(cef_name) as change, values(UNIFIsettingsSection) as section, "
        "values(src_ip) as src_ip by user "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(
        ("UniFi - Controller configuration changed", s6_query, "-70m", "now")
    )

    # 7. UniFi - Admin console access from an unexpected network
    if not dash_settings.admin_networks:
        if notes is not None:
            notes.append(
                "note: skipping 'UniFi - Admin console access from an unexpected "
                "network' (admin_networks is empty)"
            )
    else:
        cidrs_clause = " OR ".join(
            f'cidrmatch("{c}", src_ip)' for c in dash_settings.admin_networks
        )
        s7_query = (
            "eventtype=unifi_cef_admin_auth "
            f"| where NOT ({cidrs_clause}) "
            "| stats count, min(_time) as firstTime, max(_time) as lastTime, "
            "values(UNIFIaccessMethod) as method, "
            "values(src_country) as src_country by user, src_ip "
            "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
        )
        searches.append(
            (
                "UniFi - Admin console access from an unexpected network",
                s7_query,
                "-70m",
                "now",
            )
        )

    # 8. UniFi - P2P signature activity from a new host
    s8_query = (
        '`unifi_threats` rule="P2P" earliest=-1h | stats count by src '
        '| search NOT [ search `unifi_threats` rule="P2P" '
        "earliest=-8d latest=-1h "
        "| stats count by src | fields src ] | where count >= 10"
    )
    searches.append(
        ("UniFi - P2P signature activity from a new host", s8_query, "-8d", "now")
    )

    # 9. UniFi - Intrusion detection blocked
    scan_clause = ""
    if dash_settings.scan_exempt_sources:
        if len(dash_settings.scan_exempt_sources) == 1:
            src_val = dash_settings.scan_exempt_sources[0]
            scan_clause = f'NOT (rule="Scanning Activity" src="{src_val}") '
        else:
            sources_str = ", ".join(f'"{s}"' for s in dash_settings.scan_exempt_sources)
            scan_clause = f'NOT (rule="Scanning Activity" src IN ({sources_str})) '

    zone_clause = ""
    if dash_settings.intrusion_exempt_zones:
        if len(dash_settings.intrusion_exempt_zones) == 1:
            zone_expr = f'src_zone="{dash_settings.intrusion_exempt_zones[0]}"'
        else:
            zones_str = ", ".join(
                f'"{z}"' for z in dash_settings.intrusion_exempt_zones
            )
            zone_expr = f"src_zone IN ({zones_str})"
        if dash_settings.intrusion_exempt_zone_rule:
            zone_clause = (
                f'NOT (rule="{dash_settings.intrusion_exempt_zone_rule}" {zone_expr}) '
            )
        else:
            zone_clause = f"NOT ({zone_expr}) "

    s9_query = (
        "`unifi_threats` action=blocked "
        'NOT rule IN ("P2P", "TOR", "CINS Army Reputation List", '
        '"Malicious User Agents", "Malware", "Exploit Kit") '
        f"{scan_clause}"
        f"{zone_clause}"
        "| stats count, min(_time) as firstTime, max(_time) as lastTime, "
        "values(signature) as signature, values(dest_ip) as dest_ip, "
        "values(dest_port) as dest_port, values(dest_zone) as dest_zone, "
        "values(action) as action by src, rule, src_zone "
        "| `unifi_ctime(firstTime)` | `unifi_ctime(lastTime)`"
    )
    searches.append(("UniFi - Intrusion detection blocked", s9_query, "-70m", "now"))

    # 10. UniFi - Internal host blocked repeatedly
    if not dash_settings.internal_networks:
        if notes is not None:
            notes.append(
                "note: skipping 'UniFi - Internal host blocked repeatedly' "
                "(internal_networks is empty)"
            )
    else:
        cidrs_clause = " OR ".join(
            f'cidrmatch("{c}", src_ip)' for c in dash_settings.internal_networks
        )
        s10_query = (
            "`unifi_blocked` direction=local "
            f"| where {cidrs_clause} "
            "| stats count, dc(dest_ip) as targets, dc(dest_port) as ports, "
            "values(dest_ip) as dest_ip, values(dest_port) as dest_port, "
            "values(rule) as rule, values(src_network) as src_network, "
            "values(dest_network) as dest_network by src_ip | where count >= 20"
        )
        searches.append(
            ("UniFi - Internal host blocked repeatedly", s10_query, "-10m", "now")
        )

    # 11. UniFi - Syslog export has gone quiet
    s11_query = (
        "`unifi_events` | stats count | where count = 0 "
        '| eval description = "No UniFi syslog events reached Splunk '
        "in the last six hours. "
        'Check console export configuration and syslog listeners."'
    )
    searches.append(("UniFi - Syslog export has gone quiet", s11_query, "-6h", "now"))

    return searches


CORRELATION_SEARCHES: tuple[tuple[str, str, str, str], ...] = tuple(
    build_correlation_searches(DashboardSettings())
)


def load_config(path: Path | None = None) -> ToolConfig:
    if path is not None:
        if not path.is_file():
            raise ValueError(f"configuration file not found: {path}")
        with path.open("rb") as f:
            data = tomllib.load(f)
        return parse_config(data, path.parent)

    candidate = Path(CONFIG_NAME)
    if candidate.is_file():
        with candidate.open("rb") as f:
            return parse_config(tomllib.load(f), candidate.parent)

    candidate = Path(__file__).with_name(CONFIG_NAME)
    if candidate.is_file():
        with candidate.open("rb") as f:
            return parse_config(tomllib.load(f), candidate.parent)

    candidate = Path(__file__).with_name(EXAMPLE_CONFIG_NAME)
    if candidate.is_file():
        with candidate.open("rb") as f:
            return parse_config(tomllib.load(f), candidate.parent)

    return ToolConfig()


# --- App skeleton builder --------------------------------------------------


def generate_app_files(
    dash_settings: DashboardSettings, notes: list[str] | None = None
) -> dict[str, str]:
    flow_json, flow_xml = flows_dashboard()
    threat_json, threat_xml = threats_dashboard()
    client_json, client_xml = clients_dashboard()

    flow_idx = f"index={dash_settings.flow_index} " if dash_settings.flow_index else ""
    event_idx = (
        f"index={dash_settings.event_index} " if dash_settings.event_index else ""
    )

    flow_macro = f"{flow_idx}sourcetype={dash_settings.flow_sourcetype}"
    event_macro = f"{event_idx}sourcetype={dash_settings.event_sourcetype}"

    files: dict[str, str] = {}

    # Standalone JSON definitions
    files["unifi_flow_insights.json"] = json.dumps(flow_json, indent=2) + "\n"
    files["unifi_threat_center.json"] = json.dumps(threat_json, indent=2) + "\n"
    files["unifi_client_activity.json"] = json.dumps(client_json, indent=2) + "\n"
    files["dashboards/unifi_flow_insights.json"] = files["unifi_flow_insights.json"]
    files["dashboards/unifi_threat_center.json"] = files["unifi_threat_center.json"]
    files["dashboards/unifi_client_activity.json"] = files["unifi_client_activity.json"]

    # Splunk view XML documents
    files["default/data/ui/views/unifi_flow_insights.xml"] = flow_xml
    files["default/data/ui/views/unifi_threat_center.xml"] = threat_xml
    files["default/data/ui/views/unifi_client_activity.xml"] = client_xml

    # App navigation
    files["default/data/ui/nav/default.xml"] = (
        '<nav search_view="search" color="#3987e5">\n'
        '  <view name="unifi_flow_insights" default="true"/>\n'
        '  <view name="unifi_threat_center"/>\n'
        '  <view name="unifi_client_activity"/>\n'
        '  <view name="search"/>\n'
        "</nav>\n"
    )

    # App manifest
    files["default/app.conf"] = (
        "[install]\n"
        "state = enabled\n"
        "is_configured = 1\n"
        "\n"
        "[ui]\n"
        "is_visible = 1\n"
        "label = UniFi Dashboards\n"
        "\n"
        "[launcher]\n"
        "description = Traffic flow, threat, client, and health dashboards "
        "over UniFi data.\n"
        "version = 1.0.0\n"
        "\n"
        "[package]\n"
        "id = unifi_dashboards\n"
    )

    # Macros
    files["default/macros.conf"] = (
        "# Shared search macros for UniFi dashboards and correlation rules.\n"
        "\n"
        "[unifi_flows]\n"
        f"definition = {flow_macro}\n"
        "iseval = 0\n"
        "\n"
        "[unifi_events]\n"
        f"definition = {event_macro}\n"
        "iseval = 0\n"
        "\n"
        "[unifi_threats]\n"
        f"definition = {event_macro} sc4s_class IN (200, 201)\n"
        "iseval = 0\n"
        "\n"
        "[unifi_blocked]\n"
        f"definition = {flow_macro} action=blocked\n"
        "iseval = 0\n"
        "\n"
        "[fmt_bytes(1)]\n"
        "args = field\n"
        "definition = eval $field$ = case("
        '$field$ >= 1099511627776, round($field$/1099511627776,2)." TB", '
        '$field$ >= 1073741824, round($field$/1073741824,2)." GB", '
        '$field$ >= 1048576, round($field$/1048576,2)." MB", '
        '$field$ >= 1024, round($field$/1024,2)." KB", '
        '1==1, $field$." B")\n'
        "iseval = 0\n"
        "\n"
        "[unifi_ctime(1)]\n"
        "args = field\n"
        'definition = fieldformat $field$=strftime($field$, "%Y-%m-%d %H:%M:%S")\n'
        "iseval = 0\n"
    )

    # Alert actions
    files["default/alert_actions.conf"] = (
        f"[default]\nhostname = {dash_settings.external_url}\n"
    )

    # Metadata
    files["metadata/default.meta"] = (
        "[]\n"
        "access = read : [ * ], write : [ admin, sc_admin, ess_admin ]\n"
        "export = system\n"
    )

    # Props
    files["default/props.conf"] = (
        f"[{dash_settings.flow_sourcetype}]\n"
        "KV_MODE = json\n"
        "SHOULD_LINEMERGE = false\n"
        "TRUNCATE = 12000\n"
        'EVAL-severity = case(risk=="high","high", risk=="medium","medium", '
        'risk=="low","low", 1==1, "informational")\n'
        'EVAL-severity_id = case(risk=="high",3, risk=="medium",2, '
        'risk=="low",1, 1==1, 0)\n'
        'EVAL-vendor_product = "Ubiquiti UniFi Network"\n'
        "LOOKUP-unifi_dest_country = geo_attr_countries iso2 AS dest_region "
        "OUTPUT country AS dest_country\n"
        "LOOKUP-unifi_src_country = geo_attr_countries iso2 AS src_region "
        "OUTPUT country AS src_country\n"
        "\n"
        f"[{dash_settings.event_sourcetype}]\n"
        r"EXTRACT-unifi_signature = UNIFIipsSignature="
        r"(?<signature>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_rule = UNIFIpolicyName="
        r"(?<rule>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_dvc_name = UNIFIdeviceName="
        r"(?<dvc_name>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_gateway = UNIFIhost="
        r"(?<unifi_gateway>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_src_alias = UNIFIsrcClientAlias="
        r"(?<src_alias>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_dst_alias = UNIFIdstClientAlias="
        r"(?<dest_alias>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_client_alias = UNIFIclientAlias="
        r"(?<client_alias>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_wifi = UNIFIwifiName="
        r"(?<wifi_name>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_devmodel = UNIFIdeviceModel="
        r"(?<dvc_model>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_outif = deviceOutboundInterface="
        r"(?<outbound_interface>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-unifi_inif = deviceInboundInterface="
        r"(?<inbound_interface>.+?)(?=\s+[A-Za-z][A-Za-z0-9]*=|$)"
        "\n"
        r"EXTRACT-cef_header = CEF:\d+\|(?<cef_vendor>[^|]*)\|"
        r"(?<cef_product>[^|]*)\|(?<cef_device_version>[^|]*)\|"
        r"(?<cef_class_id>[^|]*)\|(?<cef_name>[^|]*)\|"
        r"(?<cef_severity>[^|]*)\|"
        "\n"
        "FIELDALIAS-unifi_src = src AS src_ip\n"
        "FIELDALIAS-unifi_dest = dst AS dest_ip\n"
        "FIELDALIAS-unifi_ports = spt AS src_port, dpt AS dest_port\n"
        "FIELDALIAS-unifi_action = act AS action\n"
        "FIELDALIAS-unifi_sigid = UNIFIipsSignatureId AS signature_id\n"
        "FIELDALIAS-unifi_zones = UNIFIsrcZone AS src_zone, "
        "UNIFIdstZone AS dest_zone\n"
        "FIELDALIAS-unifi_regions = UNIFIsrcRegion AS src_region, "
        "UNIFIdstRegion AS dest_region\n"
        "FIELDALIAS-unifi_srcmac = UNIFIsrcClientMac AS src_mac\n"
        "FIELDALIAS-unifi_dstmac = UNIFIdstClientMac AS dest_mac\n"
        "FIELDALIAS-unifi_bytes = UNIFItotalBytes AS bytes, "
        "UNIFIbytesSent AS bytes_out, UNIFIbytesReceived AS bytes_in\n"
        "FIELDALIAS-unifi_packets = UNIFItotalPackets AS packets, "
        "UNIFIpacketsSent AS packets_out, "
        "UNIFIpacketsReceived AS packets_in\n"
        "EVAL-transport = lower(proto)\n"
        'EVAL-vendor_product = coalesce(sc4s_vendor,"Ubiquiti") . " " . '
        'coalesce(sc4s_product,"UniFi Network")\n'
        "EVAL-dvc = coalesce(dvc_name, unifi_gateway)\n"
        "EVAL-src = coalesce(src_alias, UNIFIsrcClientHostname, "
        "client_alias, UNIFIclientHostname, src)\n"
        "EVAL-dest = coalesce(dest_alias, UNIFIdstClientHostname, dst)\n"
        "EVAL-user = coalesce(UNIFIadmin, suser)\n"
        'EVAL-severity = case(UNIFIrisk=="high","high", '
        'UNIFIrisk=="medium","medium", UNIFIrisk=="low","low", '
        'tonumber(cef_severity)>=9,"critical", '
        'tonumber(cef_severity)>=7,"high", '
        'tonumber(cef_severity)>=5,"medium", '
        'tonumber(cef_severity)>=3,"low", 1==1,"informational")\n'
        'EVAL-severity_id = case(UNIFIrisk=="high",3, '
        'UNIFIrisk=="medium",2, UNIFIrisk=="low",1, '
        "tonumber(cef_severity)>=9,4, tonumber(cef_severity)>=7,3, "
        "tonumber(cef_severity)>=5,2, tonumber(cef_severity)>=3,1, 1==1,0)\n"
        'EVAL-ids_type = "network"\n'
        "EVAL-app = coalesce(app, UNIFIapplication)\n"
        "EVAL-risk = UNIFIrisk\n"
        "EVAL-direction = UNIFIdirection\n"
        "LOOKUP-unifi_cef_dest_country = geo_attr_countries iso2 AS "
        "UNIFIdstRegion OUTPUT country AS dest_country\n"
        "LOOKUP-unifi_cef_src_country = geo_attr_countries iso2 AS "
        "UNIFIsrcRegion OUTPUT country AS src_country\n"
        "EVAL-subject = cef_name\n"
        "EVAL-body = _raw\n"
        'EVAL-type = "alert"\n'
    )

    # Event types
    files["default/eventtypes.conf"] = (
        "[unifi_flow_traffic]\n"
        f"search = {flow_macro}\n"
        "\n"
        "[unifi_cef_firewall]\n"
        f"search = {event_macro} sc4s_class=203\n"
        "\n"
        "[unifi_cef_ids]\n"
        f"search = {event_macro} sc4s_class IN (200, 201)\n"
        "\n"
        "[unifi_cef_admin_auth]\n"
        f"search = {event_macro} sc4s_class IN (544, 1000, 2008)\n"
        "\n"
        "[unifi_cef_config_change]\n"
        f"search = {event_macro} sc4s_class IN ("
        "510, 545, 546, 547, 548, 549, 578, 1005, 1103, 2163, 2305, 2308)\n"
        "\n"
        "[unifi_cef_session_start]\n"
        f"search = {event_macro} sc4s_class IN (400, 403, 520, 2168)\n"
        "\n"
        "[unifi_cef_session_end]\n"
        f"search = {event_macro} sc4s_class IN (401, 404, 521, 2150)\n"
        "\n"
        "[unifi_cef_device_alert]\n"
        f"search = {event_macro} sc4s_class IN ("
        "100, 107, 112, 113, 414, 512, 513, 528, 530, 539)\n"
        "\n"
        "[unifi_protect_detection]\n"
        f"search = {event_macro} sc4s_class IN (2159, 2161)\n"
    )

    # Tags
    files["default/tags.conf"] = (
        "[eventtype=unifi_flow_traffic]\n"
        "network = enabled\n"
        "communicate = enabled\n"
        "\n"
        "[eventtype=unifi_cef_firewall]\n"
        "network = enabled\n"
        "communicate = enabled\n"
        "\n"
        "[eventtype=unifi_cef_ids]\n"
        "ids = enabled\n"
        "attack = enabled\n"
        "\n"
        "[eventtype=unifi_cef_admin_auth]\n"
        "authentication = enabled\n"
        "\n"
        "[eventtype=unifi_cef_config_change]\n"
        "change = enabled\n"
        "audit = enabled\n"
        "\n"
        "[eventtype=unifi_cef_session_start]\n"
        "network = enabled\n"
        "session = enabled\n"
        "start = enabled\n"
        "\n"
        "[eventtype=unifi_cef_session_end]\n"
        "network = enabled\n"
        "session = enabled\n"
        "end = enabled\n"
        "\n"
        "[eventtype=unifi_cef_device_alert]\n"
        "alert = enabled\n"
        "\n"
        "[eventtype=unifi_protect_detection]\n"
        "alert = enabled\n"
    )

    # Saved searches
    saved_lines = [
        "# Correlation searches for UniFi threat and activity detection.",
        "",
    ]
    searches = build_correlation_searches(dash_settings, notes=notes)
    for name, search_query, earliest, latest in searches:
        saved_lines.extend(
            [
                f"[{name}]",
                f"search = {search_query}",
                f"dispatch.earliest_time = {earliest}",
                f"dispatch.latest_time = {latest}",
                "enableSched = 1",
                "counttype = number of events",
                "relation = greater than",
                "quantity = 0",
                "",
            ]
        )
    files["default/savedsearches.conf"] = "\n".join(saved_lines)

    return files


FORBIDDEN_SPL_COMMANDS: frozenset[str] = frozenset(
    {
        "outputlookup",
        "outputcsv",
        "collect",
        "tscollect",
        "sendemail",
        "delete",
        "script",
        "run",
        "sendalert",
        "meventcollect",
        "mcollect",
        "dump",
    }
)


def redact_credentials(text: str, secrets: Iterable[str | None] = ()) -> str:
    """Redact sensitive credential values and Authorization headers from text."""
    if not text:
        return text

    # Redact Authorization header patterns: Bearer <val>, Basic <val>
    result = re.sub(
        r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s,;'\"]+",
        r"\1[REDACTED]",
        text,
    )
    result = re.sub(
        r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*",
        r"\1[REDACTED]",
        result,
    )
    result = re.sub(
        r"(?i)\b(basic\s+)[A-Za-z0-9._~+/-]+=*",
        r"\1[REDACTED]",
        result,
    )

    # Redact exact secret strings
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")

    return result


def extract_pipeline_commands(query: str) -> list[str]:
    """Extract pipeline command names from an SPL search string."""
    cleaned = re.sub(r"```.*?```", " ", query, flags=re.DOTALL)
    commands: list[str] = []
    current_stage: list[str] = []
    in_quote: str | None = None
    escape = False

    for char in cleaned:
        if escape:
            current_stage.append(char)
            escape = False
            continue
        if char == "\\":
            escape = True
            current_stage.append(char)
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            current_stage.append(char)
            continue
        if char in ('"', "'"):
            in_quote = char
            current_stage.append(char)
            continue

        if char in ("|", "[", "]"):
            stage_str = "".join(current_stage).strip()
            if stage_str:
                commands.append(stage_str)
            current_stage = []
        else:
            current_stage.append(char)

    last_stage = "".join(current_stage).strip()
    if last_stage:
        commands.append(last_stage)

    cmd_names: list[str] = []
    for stage in commands:
        words = stage.split()
        if words:
            first_word = words[0].strip("()[]{};,").lower()
            cmd_names.append(first_word)

    return cmd_names


def find_forbidden_command(query: str) -> str | None:
    for cmd in extract_pipeline_commands(query):
        if cmd in FORBIDDEN_SPL_COMMANDS:
            return cmd
    return None


# --- Execution logic -------------------------------------------------------


def run_build(
    config: ToolConfig,
    force: bool = False,
    output_dir_override: Path | None = None,
) -> int:
    out_dir = output_dir_override or config.dashboards.output_dir
    notes: list[str] = []
    files = generate_app_files(config.dashboards, notes=notes)
    for note in notes:
        print(note)

    conflicts: list[str] = []
    for rel_path in sorted(files):
        target = out_dir / rel_path
        if target.exists():
            conflicts.append(str(target))

    if conflicts and not force:
        print(
            f"refusing to overwrite existing files in {out_dir}; "
            "use --force to overwrite:",
            file=sys.stderr,
        )
        for c in conflicts[:5]:
            print(f"  {c}", file=sys.stderr)
        if len(conflicts) > 5:
            print(f"  ... and {len(conflicts) - 5} more", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    for rel_path, content in files.items():
        dest = out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest.open(mode, encoding="utf-8") as f:
                f.write(content)
        except FileExistsError:
            print(
                f"refusing to overwrite existing file in {out_dir}: {dest}; "
                "use --force to overwrite",
                file=sys.stderr,
            )
            return 1

    print(f"successfully built {len(files)} files into {out_dir}")
    return 0


def collect_queries(
    app_dir: Path | None = None,
    dash_settings: DashboardSettings | None = None,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
    """Collect (dashboard, key, query) and (name, query, earliest, latest)."""
    panels: list[tuple[str, str, str]] = []
    searches: list[tuple[str, str, str, str]] = []

    if app_dir is not None:
        if not app_dir.is_dir():
            raise ValueError(f"app directory not found or not a directory: {app_dir}")

        # Check views
        view_dir = app_dir / "default" / "data" / "ui" / "views"
        if not view_dir.is_dir():
            view_dir = app_dir
        xml_files = sorted(view_dir.glob("*.xml"))
        for xml_file in xml_files:
            try:
                content = xml_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(
                    f"cannot read dashboard view {xml_file}: {exc}"
                ) from exc

            match = re.search(
                r"<definition><!\[CDATA\[\s*(.*?)\s*\]\]></definition>",
                content,
                re.DOTALL,
            )
            if not match:
                raise ValueError(
                    f"malformed dashboard definition in {xml_file}: "
                    "missing <definition> CDATA block"
                )
            try:
                definition = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"malformed dashboard JSON definition in {xml_file}: {exc}"
                ) from exc

            if not isinstance(definition, dict):
                raise ValueError(
                    f"malformed dashboard definition in {xml_file}: "
                    "JSON root is not an object"
                )

            for k, s in sorted(definition.get("dataSources", {}).items()):
                if isinstance(s, dict):
                    query = s.get("options", {}).get("query")
                    if query:
                        panels.append((xml_file.stem, k, query))

        # Check saved searches
        ss_path = app_dir / "default" / "savedsearches.conf"
        if not ss_path.is_file():
            ss_path = app_dir / "savedsearches.conf"
        if ss_path.is_file():
            try:
                ss_content = ss_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(
                    f"cannot read saved searches file {ss_path}: {exc}"
                ) from exc

            current_name: str | None = None
            current_search: str | None = None
            current_earliest = "-70m"
            current_latest = "now"
            for line in ss_content.splitlines():
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    if current_name and current_search:
                        searches.append(
                            (
                                current_name,
                                current_search,
                                current_earliest,
                                current_latest,
                            )
                        )
                    current_name = line[1:-1]
                    current_search = None
                    current_earliest = "-70m"
                    current_latest = "now"
                elif line.startswith("search = ") and current_name:
                    current_search = line[len("search = ") :]
                elif line.startswith("dispatch.earliest_time = ") and current_name:
                    current_earliest = line[len("dispatch.earliest_time = ") :]
                elif line.startswith("dispatch.latest_time = ") and current_name:
                    current_latest = line[len("dispatch.latest_time = ") :]
            if current_name and current_search:
                searches.append(
                    (
                        current_name,
                        current_search,
                        current_earliest,
                        current_latest,
                    )
                )

        if not panels and not searches:
            raise ValueError(
                f"no dashboard panel queries or saved searches found "
                f"in app directory: {app_dir}"
            )

        return panels, searches

    # Fall back to built-in dashboards only when app_dir is None
    for dash_func, dash_name in (
        (flows_dashboard, "unifi_flow_insights"),
        (threats_dashboard, "unifi_threat_center"),
        (clients_dashboard, "unifi_client_activity"),
    ):
        def_dict, _ = dash_func()
        for k, s in sorted(def_dict.get("dataSources", {}).items()):
            query = s.get("options", {}).get("query")
            if query:
                panels.append((dash_name, k, query))

    searches.extend(build_correlation_searches(dash_settings or DashboardSettings()))
    return panels, searches


def run_verify(
    config: ToolConfig,
    app_dir: Path | None = None,
    earliest: str = "-24h",
    latest: str = "now",
    fail_on_empty: bool = False,
) -> int:
    token: str | None = None
    if config.splunk.auth_token_env:
        token = os.environ.get(config.splunk.auth_token_env)

    password: str | None = None
    if config.splunk.password_env:
        password = os.environ.get(config.splunk.password_env)

    if not token and not (config.splunk.username and password):
        print(
            "authentication-error: neither token nor password found "
            "in specified environment variables",
            file=sys.stderr,
        )
        return 1

    if token:
        auth_header = f"Bearer {token}"
    else:
        raw_creds = f"{config.splunk.username}:{password}".encode()
        auth_header = "Basic " + base64.b64encode(raw_creds).decode("ascii")

    secrets_to_redact: list[str] = [s for s in (token, password, auth_header) if s]

    if not config.splunk.verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ca_path = str(config.splunk.ca_file) if config.splunk.ca_file else None
        ctx = ssl.create_default_context(cafile=ca_path)

    base_url = config.splunk.url.rstrip("/")
    app_ctx = config.splunk.app.strip("/")
    endpoint = f"{base_url}/servicesNS/nobody/{app_ctx}/search/jobs"

    def execute_query(
        query: str, start_time: str, end_time: str
    ) -> tuple[int | None, str | None]:
        forbidden = find_forbidden_command(query)
        if forbidden:
            return (
                None,
                f"rejected query containing state-changing SPL command '{forbidden}'",
            )

        search_expr = query if query.lstrip().startswith("|") else f"search {query}"
        data = urllib.parse.urlencode(
            {
                "search": search_expr,
                "earliest_time": start_time,
                "latest_time": end_time,
                "exec_mode": "oneshot",
                "output_mode": "json",
                "count": "0",
            }
        ).encode()

        req = urllib.request.Request(endpoint, data=data)
        req.add_header("Authorization", auth_header)

        try:
            with urllib.request.urlopen(
                req, context=ctx, timeout=config.splunk.timeout_seconds
            ) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", "replace")
                err_payload = json.loads(err_body)
                messages = [
                    m.get("text", "")
                    for m in err_payload.get("messages", [])
                    if isinstance(m, dict) and m.get("type") in ("ERROR", "FATAL")
                ]
                if messages:
                    raw_msg = f"HTTP {exc.code}: {'; '.join(messages)[:160]}"
                    return None, redact_credentials(raw_msg, secrets_to_redact)
            except Exception:
                pass
            return None, redact_credentials(
                f"HTTP {exc.code}: {exc.reason}", secrets_to_redact
            )
        except Exception as exc:
            return None, redact_credentials(str(exc), secrets_to_redact)

        try:
            payload = json.loads(body)
        except Exception:
            return None, "invalid JSON returned by Splunk REST endpoint"

        if not isinstance(payload, dict):
            return None, "unexpected response type returned by Splunk REST endpoint"

        fatal = [
            m
            for m in payload.get("messages", [])
            if isinstance(m, dict) and m.get("type") in ("ERROR", "FATAL")
        ]
        if fatal:
            msg_text = fatal[0].get("text", "Unknown search error")[:160]
            return None, redact_credentials(msg_text, secrets_to_redact)

        if "results" not in payload or not isinstance(payload["results"], list):
            return None, "response missing 'results' array from Splunk REST endpoint"

        results = payload["results"]
        return len(results), None

    try:
        panels, searches = collect_queries(app_dir, config.dashboards)
    except Exception as exc:
        print(
            f"verify-error: {redact_credentials(str(exc), secrets_to_redact)}",
            file=sys.stderr,
        )
        return 1

    total = 0
    ok_count = 0
    empty_count = 0
    fail_count = 0

    print("== dashboard panel queries ==")
    for dash_name, key, query in panels:
        total += 1
        rows, err = execute_query(query, earliest, latest)
        if err:
            fail_count += 1
            print(
                f"  FAIL {dash_name} [{key}]: "
                f"{redact_credentials(err, secrets_to_redact)}"
            )
        elif rows == 0:
            empty_count += 1
            print(f"  EMPTY {dash_name} [{key}]: 0 rows")
        else:
            ok_count += 1
            print(f"  OK {dash_name} [{key}]: {rows} rows")

    print("\n== correlation searches ==")
    for search_name, query, s_earliest, s_latest in searches:
        total += 1
        rows, err = execute_query(query, s_earliest, s_latest)
        if err:
            fail_count += 1
            print(f"  FAIL {search_name}: {redact_credentials(err, secrets_to_redact)}")
        elif rows == 0:
            empty_count += 1
            print(f"  EMPTY {search_name}: 0 rows")
        else:
            ok_count += 1
            print(f"  OK {search_name}: {rows} rows")

    print(
        f"\nsummary: {total} total, {ok_count} with rows, "
        f"{empty_count} empty, {fail_count} failed"
    )

    if fail_count > 0:
        return 1
    if fail_on_empty and empty_count > 0:
        return 1
    return 0


# --- CLI Parser ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate and verify Splunk Dashboard Studio views for UniFi flow data."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_p = subparsers.add_parser(
        "build", help="generate Dashboard Studio JSON and Splunk app skeleton"
    )
    build_p.add_argument(
        "--config", "-c", type=Path, help="path to configuration TOML file"
    )
    build_p.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        help="output directory for generated files",
    )
    build_p.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing files in output directory",
    )
    build_p.add_argument("--flow-index", help="override index for flow events")
    build_p.add_argument(
        "--flow-sourcetype", help="override sourcetype for flow events"
    )
    build_p.add_argument("--event-index", help="override index for syslog events")
    build_p.add_argument(
        "--event-sourcetype", help="override sourcetype for syslog events"
    )
    build_p.add_argument("--external-url", help="override external base URL for alerts")

    verify_p = subparsers.add_parser(
        "verify", help="run dashboard and correlation queries against Splunk REST"
    )
    verify_p.add_argument(
        "--config", "-c", type=Path, help="path to configuration TOML file"
    )
    verify_p.add_argument("--splunk-url", help="override Splunk REST endpoint URL")
    verify_p.add_argument("--app", help="override Splunk application context")
    verify_p.add_argument(
        "--auth-token-env", help="override env variable name for Splunk token"
    )
    verify_p.add_argument("--username", help="Splunk username for basic authentication")
    verify_p.add_argument(
        "--password-env", help="override env variable name for password"
    )
    verify_p.add_argument(
        "--ca-file", type=Path, help="path to custom CA certificate bundle"
    )
    verify_p.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification",
    )
    verify_p.add_argument("--timeout", type=float, help="request timeout in seconds")
    verify_p.add_argument(
        "--app-dir",
        type=Path,
        help="path to app directory containing views and savedsearches",
    )
    verify_p.add_argument(
        "--earliest",
        default="-24h",
        help="earliest time boundary for panel queries (default: -24h)",
    )
    verify_p.add_argument(
        "--latest",
        default="now",
        help="latest time boundary for panel queries (default: now)",
    )
    verify_p.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="treat empty query results as failures",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(getattr(args, "config", None))
    except Exception as exc:
        print(f"config-error: {redact_credentials(str(exc))}", file=sys.stderr)
        return 1

    if args.command == "build":
        out_dir = args.output_dir or config.dashboards.output_dir
        flow_idx = args.flow_index or config.dashboards.flow_index
        flow_st = args.flow_sourcetype or config.dashboards.flow_sourcetype
        event_idx = args.event_index or config.dashboards.event_index
        event_st = args.event_sourcetype or config.dashboards.event_sourcetype
        ext_url = args.external_url or config.dashboards.external_url

        updated_dash = replace(
            config.dashboards,
            output_dir=out_dir,
            flow_index=flow_idx,
            flow_sourcetype=flow_st,
            event_index=event_idx,
            event_sourcetype=event_st,
            external_url=ext_url,
        )
        updated_config = ToolConfig(splunk=config.splunk, dashboards=updated_dash)
        return run_build(updated_config, force=args.force, output_dir_override=out_dir)

    if args.command == "verify":
        s_url = args.splunk_url or config.splunk.url
        s_app = args.app or config.splunk.app
        token_env = args.auth_token_env or config.splunk.auth_token_env
        user = args.username if args.username is not None else config.splunk.username
        pass_env = args.password_env or config.splunk.password_env
        verify_tls = False if args.insecure else config.splunk.verify_tls
        ca_file = args.ca_file or config.splunk.ca_file
        timeout = (
            args.timeout if args.timeout is not None else config.splunk.timeout_seconds
        )

        updated_splunk = SplunkSettings(
            url=s_url,
            app=s_app,
            auth_token_env=token_env,
            username=user,
            password_env=pass_env,
            verify_tls=verify_tls,
            ca_file=ca_file,
            timeout_seconds=timeout,
        )
        updated_config = ToolConfig(splunk=updated_splunk, dashboards=config.dashboards)
        return run_verify(
            updated_config,
            app_dir=args.app_dir,
            earliest=args.earliest,
            latest=args.latest,
            fail_on_empty=args.fail_on_empty,
        )

    return 1


if __name__ == "__main__":
    sys.exit(main())
