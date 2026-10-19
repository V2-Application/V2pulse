"""V2Pulse shim for the DFS (sale-plan) ml-engine connection helpers the MIS code imports.

DFS opens its own Snowflake session per call; in V2Pulse every MIS session uses the "mis" profile of
pulse.data.snowflake (PULSE_SVC with the MIS role and warehouses from env.bat). The MIS code itself is a
copy of DFS ml-engine (GitHub V2-Application/sale-plan, main) - see pulse/mis_engine/README.txt."""
from ...data.snowflake import connect

SF_SCHEMA = "BRONZE"


def get_sf_connection(warehouse=None):
    """Compute connection (reports, loads). `warehouse` = 'read' | 'compute' | a warehouse name."""
    return connect(profile="mis", warehouse=warehouse or "compute", tag={"module": "mis"})


def get_sf_read_connection():
    """Interactive reads (dropdowns, filter lists)."""
    return connect(profile="mis", warehouse="read", tag={"module": "mis"})
