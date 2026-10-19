"""Settings, read from environment variables prefixed PULSE_ (env.bat on the server)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PULSE_", env_file=".env", extra="ignore")

    env: str = "dev"                      # dev | prod ; dev enables the dev login
    base_url: str = "http://127.0.0.1:8875"
    port: int = 8875

    database_url: str = "postgresql://pulse_app:pulse_app@127.0.0.1:55432/pulse"
    database_owner_url: str = "postgresql://pulse_owner:pulse_owner@127.0.0.1:55432/pulse"
    db_pool_max: int = 20

    cookie_secure: bool = False           # True once served over HTTPS
    session_idle_hours: float = 10
    session_absolute_days: float = 7
    admin_session_hours: float = 4

    # Entra ID (Microsoft) sign-in for HO staff. Empty tenant/client = provider hidden.
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""

    local_login_enabled: bool = True      # break-glass employee code + password
    local_max_failures: int = 5
    local_lock_minutes: int = 15

    # V2 Parivar (HRMS) sign-in: employees use their existing Parivar employee code + password. V2Pulse
    # checks them against the Parivar login API at sign-in and NEVER stores the password. Empty URL = hidden.
    parivar_login_url: str = ""           # e.g. https://v2parivar.v2retail.com:9987/api/Auth/loginnew
    parivar_user_field: str = "empCode"   # JSON field for the employee code in the login request
    parivar_pass_field: str = "password"  # JSON field for the password
    parivar_success_field: str = ""       # optional JSON field that must be truthy on success (else any 2xx = ok)
    parivar_verify_tls: bool = True       # set false only if the HRMS TLS certificate cannot be validated yet
    parivar_timeout_s: float = 12.0

    # Network allowlist (LAN only in Phase 0). XFF is trusted only from these proxies.
    allowed_nets: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1/128"
    trusted_proxies: str = ""

    # Snowflake: V2Pulse's own service identity (reads V2RETAIL.PULSE views).
    sf_account: str = "iafphkw-hh80816"
    sf_user: str = "PULSE_SVC"
    sf_key_path: str = r"D:\Mahesh\Python\v2pulse\keys\pulse_svc_rsa.p8"
    sf_role: str = "PULSE_SVC_ROLE"
    sf_warehouse: str = "COMPUTE_WH"
    sf_source_schema: str = "V2RETAIL.PULSE"   # where the curated views live

    # Snowflake: the Report Portal identity, reused by the Control Tower collector.
    ct_sf_user: str = "REPORT_PORTAL_SVC"
    ct_sf_key_path: str = r"D:\Mahesh\Python\report-portal\keys\report_portal_svc_rsa.p8"
    ct_sf_role: str = "REPORT_PORTAL_SVC_ROLE"
    ct_sf_warehouse: str = "COMPUTE_WH"

    # Snowflake: MIS module (copy of the DFS / sale-plan MIS). Same PULSE_SVC user with its own role, which
    # holds the grants on the MIS sources (BRONZE day-wise facts, AKS_* masters) and the MIS warehouses.
    mis_sf_role: str = "PULSE_MIS_ROLE"
    mis_read_warehouse: str = "AKS_WH_XS"       # interactive reads (dropdowns, filter lists) - DFS SF_READ_WAREHOUSE
    mis_compute_warehouse: str = "AKS_WH_L"     # report runs, exports, loads - DFS SF_COMPUTE_WAREHOUSE
    mis_statement_timeout_s: int = 3600
    mis_template_dir: str = r"D:\V2PulseData\mis-templates"   # report Excel templates (<slug>.xlsx + .meta.json)

    # Dump Scheduler: scheduled CSV / Excel exports to a folder per schedule.
    dump_max_concurrency: int = 2
    dump_default_timeout_min: int = 60
    dump_keep_days: int = 90              # run history and mail log
    # Failure e-mail (SMTP). Empty host = no e-mail (failures still show on the page).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    dump_fail_mail_cc: str = ""           # comma list, added to every failure mail

    hr_max_deactivate_pct: float = 2.0    # HR sync refuses to mark more users as left in one run
    bootstrap_admins: str = ""            # comma list of employee codes made PORTAL_ADMIN with company scope
    pg_bin: str = r"D:\Mahesh\Python\v2pulse\pgsql\bin"
    backup_dir: str = r"D:\V2PulseData\backup"
    backup_keep_days: int = 14
    files_root: str = r"\\DataCenter.v2rd.com\Python\sf-daily-exports"   # kind=file reports must live under here
    portal_check_timeout_s: float = 8.0
    worker_poll_seconds: int = 20

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def settings() -> Settings:
    return Settings()
